"""`axon index-compare` and `axon index-prune-legacy`: the verified delete (SY-14 to SY-16)."""

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


def _paths(table: str) -> set[str]:
    return {r["file_path"] for r in _fetch(f"SELECT file_path FROM {table}")}  # noqa: S608


async def _seed_legacy(rows: list[tuple[str, str]]) -> None:
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
                ctx="knowledge",
                content="legacy",
            )
            for i, (project, path) in enumerate(rows)
        ]
    )
    for _, path in rows:
        await cache.set_entry(path, "knowledge", "0" * 40, 1)
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


def test_prune_is_refused_for_a_repo_with_no_root_here(alpha: Path) -> None:
    _mixed_index(alpha)
    before = _paths("embeddings")

    result = runner.invoke(
        main_app,
        ["index-prune-legacy", "beta", "--legacy-vault-root", _LEGACY_VAULT, "--apply"],
    )

    assert result.exit_code != 0
    assert _paths("embeddings") == before
