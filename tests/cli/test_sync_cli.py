"""`axon sync`: where it runs resolves to a registered root, and `--all` reports (SY-5, SY-6)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import types
from pathlib import Path
from unittest.mock import MagicMock

import asyncpg
import pytest
from typer.testing import CliRunner

from axon.__main__ import app as main_app
from axon.cli import pb
from axon.core.file_identity import build_repo_roots
from axon.embedder.sync import resolve_root
from axon.store.pg_vector_store import VECTOR_SIZE

runner = CliRunner()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True  # noqa: S607
    ).stdout.strip()


def _repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    for name, body in files.items():
        (path / name).write_text(body, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "first")
    return path.resolve()


def _rows(sql: str) -> list[asyncpg.Record]:
    async def fetch() -> list[asyncpg.Record]:
        con = await asyncpg.connect(os.environ["AXON_PG_URL"])
        try:
            if not await con.fetchval("SELECT to_regclass('embeddings')"):
                return []
            return await con.fetch(sql)
        finally:
            await con.close()

    return asyncio.run(fetch())


def _indexed(project: str) -> set[str]:
    rows = _rows(f"SELECT DISTINCT file_path FROM embeddings WHERE project = '{project}'")  # noqa: S608
    return {r["file_path"] for r in rows}


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """A runtime with its own registry, a stub embedder and the suite's database."""
    data = tmp_path / "engine" / "data"
    data.mkdir(parents=True)
    vault = tmp_path / "vault"
    vault.mkdir()
    runtime = types.SimpleNamespace(
        engine_root=tmp_path / "engine",
        data_root=data,
        vault_root=vault,
        pg_url=os.environ["AXON_PG_URL"],
    )
    engine = MagicMock()
    engine.embed.side_effect = lambda texts: [[0.0] * VECTOR_SIZE for _ in texts]
    monkeypatch.setattr(pb, "_RUNTIME", runtime)
    monkeypatch.setattr("axon.embedder.engine.EmbedderEngine", lambda: engine)

    def register(*roots: Path) -> None:
        (data / "onboarded_repos.json").write_text(
            json.dumps([str(r) for r in roots]), encoding="utf-8"
        )

    return types.SimpleNamespace(
        tmp=tmp_path, vault=vault, engine=engine, register=register, runtime=runtime
    )


def test_resolve_root_maps_a_checkout_a_subdir_a_symlink_and_a_linked_worktree(
    tmp_path: Path,
) -> None:
    main = _repo(tmp_path / "alpha", {"README.md": "# A\n"})
    (main / "src").mkdir()
    linked = tmp_path / "wt"
    _git(main, "worktree", "add", "-b", "feature", str(linked))
    alias = tmp_path / "alias"
    alias.symlink_to(main, target_is_directory=True)
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    roots = build_repo_roots([main], vault.resolve())

    assert resolve_root(main, roots) == ("alpha", main)
    assert resolve_root(main / "src", roots) == ("alpha", main)
    assert resolve_root(linked, roots) == ("alpha", main)
    assert resolve_root(alias, roots) == ("alpha", main)
    assert resolve_root(vault / "notes", roots) == ("vault", vault.resolve())
    assert resolve_root(tmp_path, roots) is None


def test_sync_from_a_linked_worktree_indexes_the_main_checkout(
    machine: types.SimpleNamespace,
) -> None:
    main = _repo(machine.tmp / "alpha", {"README.md": "# A\nmain\n"})
    linked = machine.tmp / "wt"
    _git(main, "worktree", "add", "-b", "feature", str(linked))
    (linked / "only_in_worktree.md").write_text("# W\nbody\n", encoding="utf-8")
    _git(linked, "add", "-A")
    _git(linked, "commit", "-m", "worktree only")
    machine.register(main)

    result = runner.invoke(main_app, ["sync", str(linked), "--ctx", "knowledge"])

    assert result.exit_code == 0, result.output
    assert _indexed("alpha") == {"README.md"}


def test_sync_refuses_a_path_under_no_registered_root(
    machine: types.SimpleNamespace,
) -> None:
    stray = _repo(machine.tmp / "stray", {"README.md": "# S\n"})
    machine.register(_repo(machine.tmp / "alpha", {"README.md": "# A\n"}))

    result = runner.invoke(main_app, ["sync", str(stray), "--ctx", "knowledge"])

    assert result.exit_code != 0
    assert "axon init" in result.output
    assert _rows("SELECT 1 FROM embeddings") == []


def test_sync_all_reports_what_it_cannot_sync_and_syncs_the_rest(
    machine: types.SimpleNamespace,
) -> None:
    healthy = _repo(machine.tmp / "alpha", {"README.md": "# A\n"})
    # A linked worktree registered as a root: its name is not its repo identity.
    other = _repo(machine.tmp / "beta", {"README.md": "# B\n"})
    misnamed = machine.tmp / "beta_wt"
    _git(other, "worktree", "add", "-b", "feature", str(misnamed))
    missing = machine.tmp / "gone"
    machine.register(healthy, misnamed.resolve(), missing)
    (machine.vault / "note.md").write_text("# Note\nbody\n", encoding="utf-8")
    asyncio.run(_seed_ctx("alpha", "personal"))

    result = runner.invoke(main_app, ["sync", "--all"])

    assert result.exit_code != 0
    assert "gone" in result.output and "does not exist" in result.output
    assert "beta_wt" in result.output and "beta" in result.output
    assert _indexed("alpha") == {"README.md"}
    assert _indexed("vault") == {"note.md"}
    assert _indexed("beta_wt") == set()


def test_sync_dry_run_embeds_nothing_and_prints_files_and_characters(
    machine: types.SimpleNamespace,
) -> None:
    body = "# A\n" + "x" * 96
    main = _repo(machine.tmp / "alpha", {"README.md": body})
    machine.register(main)

    result = runner.invoke(main_app, ["sync", "--all", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert machine.engine.embed.call_count == 0
    assert _rows("SELECT 1 FROM embeddings") == []
    line = next(ln for ln in result.output.splitlines() if ln.startswith("alpha"))
    assert "1 file" in line and f"{len(body)} char" in line


async def _seed_ctx(repo: str, ctx: str) -> None:
    from axon.store.pg_file_cache import PostgresFileCache

    cache = PostgresFileCache(os.environ["AXON_PG_URL"])
    await cache.ensure_schema()
    await cache.set_repo_state(repo, ctx, None)
    await cache.close()
