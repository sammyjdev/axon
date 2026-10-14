"""`axon index-compare` and `axon index-prune-legacy`: the verified delete (SY-14 to SY-16)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import types
from pathlib import Path
from unittest.mock import MagicMock

import asyncpg
import pytest
from typer.testing import CliRunner

from axon.__main__ import app as main_app
from axon.cli import pb
from axon.embedder import legacy
from axon.store.pg_file_cache import PostgresFileCache
from axon.store.pg_vector_store import VECTOR_SIZE, PgVectorStore
from axon.store.vector_common import Chunk as VectorChunk

runner = CliRunner()
_LEGACY_VAULT = "/Users/someone/vault"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True  # noqa: S607
    ).stdout.strip()


def _fetch(sql: str) -> list[asyncpg.Record]:
    async def run() -> list[asyncpg.Record]:
        con = await asyncpg.connect(os.environ["AXON_PG_URL"])
        try:
            return await con.fetch(sql)
        finally:
            await con.close()

    return asyncio.run(run())


def _execute(sql: str) -> None:
    async def run() -> None:
        con = await asyncpg.connect(os.environ["AXON_PG_URL"])
        try:
            await con.execute(sql)
        finally:
            await con.close()

    asyncio.run(run())


def _paths(table: str) -> set[str]:
    return {r["file_path"] for r in _fetch(f"SELECT file_path FROM {table}")}  # noqa: S608


async def _seed_legacy(
    rows: list[tuple[str, str]], ctx: str = "knowledge", first_id: int = 0
) -> None:
    """Legacy rows: (project, absolute path), in both tables."""
    store = PgVectorStore(os.environ["AXON_PG_URL"])
    await store.ensure_collections()
    cache = PostgresFileCache(os.environ["AXON_PG_URL"])
    await cache.ensure_schema()
    await store.upsert_batch(
        [
            VectorChunk(
                id=f"00000000-0000-0000-0000-{i:012d}",
                vector=[0.0] * VECTOR_SIZE,
                file_path=path,
                language="markdown",
                chunk_type="file",
                symbol="s",
                project=project,
                ctx=ctx,
                content="legacy",
            )
            for i, (project, path) in enumerate(rows, start=first_id)
        ]
    )
    for _, path in rows:
        await cache.set_entry(path, ctx, "0" * 40, 1)
    await store.close()
    await cache.close()


@pytest.fixture
def alpha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A registered repo `alpha` with two files, synced; legacy rows seeded by each test."""
    data = tmp_path / "engine" / "data"
    data.mkdir(parents=True)
    vault = tmp_path / "vault"
    vault.mkdir()
    repo = tmp_path / "alpha"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.md").write_text("# A\nbody\n", encoding="utf-8")
    (repo / "b.md").write_text("# B\nbody\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "first")
    (data / "onboarded_repos.json").write_text(json.dumps([str(repo.resolve())]), encoding="utf-8")
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
    monkeypatch.setenv("AXON_ALLOW_DESTRUCTIVE", "1")
    return repo


def _mixed_index(repo: Path, alpha_legacy_files: int = 2) -> None:
    rows = [("alpha", f"/Users/someone/dev/alpha/old{i}.md") for i in range(alpha_legacy_files)]
    rows += [
        ("beta", "/Users/someone/dev/beta/x.md"),
        # A vault folder that carries a repo's name: it is the vault's, not the repo's.
        ("alpha", f"{_LEGACY_VAULT}/alpha/note.md"),
        ("inbox", f"{_LEGACY_VAULT}/inbox/n.md"),
    ]
    asyncio.run(_seed_legacy(rows))
    synced = runner.invoke(main_app, ["sync", str(repo), "--ctx", "knowledge"])
    assert synced.exit_code == 0, synced.output


def _compare() -> dict[str, list[str]]:
    result = runner.invoke(main_app, ["index-compare", "--legacy-vault-root", _LEGACY_VAULT])
    assert result.exit_code == 0, result.output
    return {ln.split()[0]: ln.split() for ln in result.output.splitlines() if ln.strip()}


def _prune(*extra: str) -> object:
    return runner.invoke(
        main_app, ["index-prune-legacy", "alpha", "--legacy-vault-root", _LEGACY_VAULT, *extra]
    )


def test_compare_counts_a_repo_and_the_vault_apart(alpha: Path) -> None:
    _mixed_index(alpha)

    table = _compare()

    # repo, old chunks, new chunks, old files, new files, then the verdict
    assert table["alpha"][1:5] == ["2", "2", "2", "2"]
    assert "eligible" in table["alpha"]
    assert table["vault"][1] == "2" and table["vault"][3] == "2"
    assert table["beta"][1] == "1"
    assert "eligible" not in table["beta"]


def test_prune_removes_only_that_repos_legacy_rows(alpha: Path) -> None:
    _mixed_index(alpha)

    result = _prune("--apply")

    assert result.exit_code == 0, result.output
    survivors = {
        "/Users/someone/dev/beta/x.md",
        f"{_LEGACY_VAULT}/alpha/note.md",
        f"{_LEGACY_VAULT}/inbox/n.md",
        "a.md",
        "b.md",
    }
    assert _paths("embeddings") == survivors
    assert _paths("file_index") == survivors


def test_prune_without_apply_deletes_nothing(alpha: Path) -> None:
    _mixed_index(alpha)
    before = _paths("embeddings")

    result = _prune()

    assert result.exit_code == 0, result.output
    assert "--apply" in result.output
    assert _paths("embeddings") == before


def test_prune_is_refused_without_destructive_consent(
    alpha: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mixed_index(alpha)
    before = _paths("embeddings")
    monkeypatch.delenv("AXON_ALLOW_DESTRUCTIVE")

    result = _prune("--apply")

    assert result.exit_code != 0
    assert "AXON_ALLOW_DESTRUCTIVE" in result.output
    assert _paths("embeddings") == before


def test_prune_is_refused_under_the_stop_line(alpha: Path) -> None:
    _mixed_index(alpha, alpha_legacy_files=5)  # 2 new files against 5 old: 40 percent
    before = _paths("embeddings")

    result = _prune("--apply")

    assert result.exit_code != 0
    assert "50" in result.output
    assert _paths("embeddings") == before


def test_prune_is_refused_when_the_index_is_behind_head(alpha: Path) -> None:
    _mixed_index(alpha)
    (alpha / "c.md").write_text("# C\nbody\n", encoding="utf-8")
    _git(alpha, "add", "-A")
    _git(alpha, "commit", "-m", "second")
    before = _paths("embeddings")

    result = _prune("--apply")

    assert result.exit_code != 0
    assert "HEAD" in result.output
    assert _paths("embeddings") == before


def test_a_prune_that_fails_midway_deletes_nothing(alpha: Path) -> None:
    _mixed_index(alpha)
    cache_before = _paths("file_index")
    chunks_before = _paths("embeddings")
    # The file_index delete runs first; the embeddings delete then fails.
    _execute(
        "CREATE FUNCTION refuse_delete() RETURNS trigger LANGUAGE plpgsql"
        " AS $$ BEGIN RAISE EXCEPTION 'delete refused'; END $$;"
        " CREATE TRIGGER refuse_delete BEFORE DELETE ON embeddings"
        " FOR EACH ROW EXECUTE FUNCTION refuse_delete();"
    )
    try:
        result = _prune("--apply")
    finally:
        _execute("DROP TRIGGER refuse_delete ON embeddings; DROP FUNCTION refuse_delete();")

    assert "delete refused" in str(result.exception)
    assert _paths("file_index") == cache_before
    assert _paths("embeddings") == chunks_before


def test_prune_keeps_the_cache_entry_of_another_ctx_on_the_same_path(alpha: Path) -> None:
    shared = "/Users/someone/dev/shared/p.md"
    asyncio.run(_seed_legacy([("alpha", shared)], ctx="knowledge"))
    asyncio.run(_seed_legacy([("beta", shared)], ctx="personal", first_id=100))
    synced = runner.invoke(main_app, ["sync", str(alpha), "--ctx", "knowledge"])
    assert synced.exit_code == 0, synced.output

    result = _prune("--apply")

    assert result.exit_code == 0, result.output
    legacy_cache = _fetch("SELECT file_path, ctx FROM file_index WHERE repo = ''")
    assert {(r["file_path"], r["ctx"]) for r in legacy_cache} == {(shared, "personal")}


def test_compare_reports_before_any_sync_created_its_table(alpha: Path) -> None:
    asyncio.run(_seed_legacy([("alpha", "/Users/someone/dev/alpha/old0.md")]))
    # The state an upgraded database is in until the first sync.
    _execute("ALTER TABLE repo_state RENAME TO repo_state_hidden")
    try:
        result = runner.invoke(main_app, ["index-compare", "--legacy-vault-root", _LEGACY_VAULT])
    finally:
        _execute("ALTER TABLE repo_state_hidden RENAME TO repo_state")

    assert result.exit_code == 0, result.output
    assert "never synced" in result.output


def test_prune_is_refused_for_a_root_with_no_git_head(alpha: Path) -> None:
    shutil.rmtree(alpha / ".git")
    _mixed_index(alpha)
    before = _paths("embeddings")

    result = _prune("--apply")

    assert result.exit_code != 0
    assert _paths("embeddings") == before


def test_prune_of_the_vault_also_needs_a_git_head(alpha: Path) -> None:
    vault = pb._RUNTIME.vault_root
    (vault / "knowledge").mkdir()
    for i in range(2):
        (vault / "knowledge" / f"n{i}.md").write_text(f"# N{i}\nbody\n", encoding="utf-8")
    asyncio.run(_seed_legacy([("inbox", f"{_LEGACY_VAULT}/inbox/n.md")]))
    synced = runner.invoke(main_app, ["sync", str(vault)])
    assert synced.exit_code == 0, synced.output
    before = _paths("embeddings")

    result = runner.invoke(
        main_app,
        ["index-prune-legacy", "vault", "--legacy-vault-root", _LEGACY_VAULT, "--apply"],
    )

    assert result.exit_code != 0
    assert "HEAD" in result.output
    assert _paths("embeddings") == before


def test_prune_holds_the_index_lock_from_the_check_to_the_delete(
    alpha: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mixed_index(alpha)
    lock = pb._RUNTIME.data_root / ".axon" / "index.lock"
    held: list[tuple[str, bool]] = []
    real_report, real_prune = legacy.legacy_report, legacy.prune_legacy

    async def report(*args, **kwargs):
        held.append(("check", lock.exists()))
        return await real_report(*args, **kwargs)

    async def prune(*args, **kwargs):
        held.append(("delete", lock.exists()))
        return await real_prune(*args, **kwargs)

    monkeypatch.setattr(legacy, "legacy_report", report)
    monkeypatch.setattr(legacy, "prune_legacy", prune)

    result = _prune("--apply")

    assert result.exit_code == 0, result.output
    assert held == [("check", True), ("delete", True)]


def test_prune_is_refused_while_another_indexer_runs(alpha: Path) -> None:
    _mixed_index(alpha)
    lock = pb._RUNTIME.data_root / ".axon" / "index.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()), encoding="utf-8")
    before = _paths("embeddings")

    result = _prune("--apply")

    assert result.exit_code != 0
    assert _paths("embeddings") == before


def test_prune_is_refused_for_a_repo_with_no_root_here(alpha: Path) -> None:
    _mixed_index(alpha)
    before = _paths("embeddings")

    result = runner.invoke(
        main_app,
        ["index-prune-legacy", "beta", "--legacy-vault-root", _LEGACY_VAULT, "--apply"],
    )

    assert result.exit_code != 0
    assert _paths("embeddings") == before
