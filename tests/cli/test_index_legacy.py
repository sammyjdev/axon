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


def _rows() -> tuple[set[str], set[str]]:
    """Both tables a prune deletes from: a refusal must leave each untouched."""
    return _paths("embeddings"), _paths("file_index")


async def _current_schema() -> None:
    cache = PostgresFileCache(os.environ["AXON_PG_URL"])
    try:
        await cache.ensure_schema()
    finally:
        await cache.close()


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
    before = _rows()

    result = _prune()

    assert result.exit_code == 0, result.output
    assert "--apply" in result.output
    assert _rows() == before


def test_prune_is_refused_without_destructive_consent(
    alpha: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mixed_index(alpha)
    before = _rows()
    monkeypatch.delenv("AXON_ALLOW_DESTRUCTIVE")

    result = _prune("--apply")

    assert result.exit_code != 0
    assert "AXON_ALLOW_DESTRUCTIVE" in result.output
    assert _rows() == before


def test_prune_is_refused_under_the_stop_line(alpha: Path) -> None:
    _mixed_index(alpha, alpha_legacy_files=5)  # 2 new files against 5 old: 40 percent
    before = _rows()

    result = _prune("--apply")

    assert result.exit_code != 0
    assert "50" in result.output
    assert _rows() == before


def test_prune_is_refused_when_the_index_is_behind_head(alpha: Path) -> None:
    _mixed_index(alpha)
    (alpha / "c.md").write_text("# C\nbody\n", encoding="utf-8")
    _git(alpha, "add", "-A")
    _git(alpha, "commit", "-m", "second")
    before = _rows()

    result = _prune("--apply")

    assert result.exit_code != 0
    assert "HEAD" in result.output
    assert _rows() == before


def test_a_prune_that_fails_midway_deletes_nothing(alpha: Path) -> None:
    _mixed_index(alpha)
    cache_before = _paths("file_index")
    chunks_before = _paths("embeddings")
    # The embeddings delete runs first; the file_index delete then fails.
    _execute(
        "CREATE FUNCTION refuse_delete() RETURNS trigger LANGUAGE plpgsql"
        " AS $$ BEGIN RAISE EXCEPTION 'delete refused'; END $$;"
        " CREATE TRIGGER refuse_delete BEFORE DELETE ON file_index"
        " FOR EACH ROW EXECUTE FUNCTION refuse_delete();"
    )
    try:
        result = _prune("--apply")
    finally:
        _execute("DROP TRIGGER refuse_delete ON file_index; DROP FUNCTION refuse_delete();")

    assert "delete refused" in str(result.exception)
    assert _paths("file_index") == cache_before
    assert _paths("embeddings") == chunks_before


def test_prune_keeps_a_cache_entry_another_repo_still_has_chunks_under(alpha: Path) -> None:
    shared = "/Users/someone/dev/shared/p.md"
    # Same path and ctx: file_index holds one row for both projects' chunks.
    asyncio.run(_seed_legacy([("alpha", shared), ("beta", shared)]))
    synced = runner.invoke(main_app, ["sync", str(alpha), "--ctx", "knowledge"])
    assert synced.exit_code == 0, synced.output

    result = _prune("--apply")

    assert result.exit_code == 0, result.output
    legacy_cache = _fetch("SELECT file_path, ctx FROM file_index WHERE repo = ''")
    assert {(r["file_path"], r["ctx"]) for r in legacy_cache} == {(shared, "knowledge")}


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


def test_compare_reports_on_a_database_from_before_this_build(alpha: Path) -> None:
    asyncio.run(_seed_legacy([("alpha", "/Users/someone/dev/alpha/old0.md")]))
    # The schema an upgraded database has until something of this build opens it:
    # no repo_state, and file_index keyed on (file_path, ctx) with no repo column.
    _execute(
        "DROP TABLE repo_state;"
        " ALTER TABLE file_index DROP CONSTRAINT file_index_repo_pkey;"
        " ALTER TABLE file_index DROP COLUMN repo;"
        " ALTER TABLE file_index ADD PRIMARY KEY (file_path, ctx);"
    )
    try:
        result = runner.invoke(main_app, ["index-compare", "--legacy-vault-root", _LEGACY_VAULT])
    finally:
        asyncio.run(_current_schema())

    assert result.exit_code == 0, result.output
    assert "never synced" in result.output


def test_prune_is_refused_for_a_repo_never_synced(alpha: Path) -> None:
    _mixed_index(alpha)
    _execute("DELETE FROM repo_state")
    before = _rows()

    result = _prune("--apply")

    assert result.exit_code != 0
    assert "never synced" in result.output
    assert _rows() == before


def test_a_pending_legacy_row_does_not_block_the_prune(alpha: Path) -> None:
    _mixed_index(alpha)
    _execute(
        "UPDATE file_index SET status = 'pending'"
        " WHERE repo = '' AND file_path LIKE '/Users/someone/dev/alpha/%'"
    )

    result = _prune("--apply")

    assert result.exit_code == 0, result.output
    assert not {p for p in _paths("embeddings") if p.startswith("/Users/someone/dev/alpha/")}


@pytest.mark.parametrize(
    "args",
    [
        ["sync", "--all"],
        ["index-compare", "--legacy-vault-root", _LEGACY_VAULT],
        ["index-prune-legacy", "alpha", "--legacy-vault-root", _LEGACY_VAULT, "--apply"],
    ],
)
def test_an_unreadable_registry_stops_the_command_before_any_store_opens(
    alpha: Path, args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _mixed_index(alpha)
    (pb._RUNTIME.data_root / "onboarded_repos.json").write_text("{not json", encoding="utf-8")
    before = _rows()

    def no_store(*args, **kwargs):
        raise AssertionError("a store was opened")

    # Every store reaches Postgres through one of these two. A scoped context, not
    # monkeypatch.undo(): undo would also drop conftest's AXON_PG_URL, and the
    # snapshot below would then read the operator's live database.
    with monkeypatch.context() as scoped:
        scoped.setattr(asyncpg, "connect", no_store)
        scoped.setattr(asyncpg, "create_pool", no_store)
        result = runner.invoke(main_app, args)

    assert "cannot be read" in str(result.exception), result.output
    assert _rows() == before


def test_prune_is_refused_for_a_root_with_no_git_head(alpha: Path) -> None:
    shutil.rmtree(alpha / ".git")
    _mixed_index(alpha)
    before = _rows()

    result = _prune("--apply")

    assert result.exit_code != 0
    assert _rows() == before


def test_an_empty_legacy_vault_root_is_refused(alpha: Path) -> None:
    vault = pb._RUNTIME.vault_root
    _git(vault, "init", "-b", "main")
    _git(vault, "config", "user.email", "t@t")
    _git(vault, "config", "user.name", "t")
    (vault / "knowledge").mkdir()
    for i in range(2):
        (vault / "knowledge" / f"n{i}.md").write_text(f"# N{i}\nbody\n", encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "notes")
    asyncio.run(_seed_legacy([("alpha", f"/Users/someone/dev/alpha/old{i}.md") for i in range(2)]))
    synced = runner.invoke(main_app, ["sync", str(vault)])
    assert synced.exit_code == 0, synced.output
    before = _rows()

    # An empty root would make "/" the vault prefix, and every legacy row the vault's.
    result = runner.invoke(
        main_app, ["index-prune-legacy", "vault", "--legacy-vault-root", "", "--apply"]
    )

    assert result.exit_code != 0
    assert _rows() == before


def test_prune_of_the_vault_also_needs_a_git_head(alpha: Path) -> None:
    vault = pb._RUNTIME.vault_root
    (vault / "knowledge").mkdir()
    for i in range(2):
        (vault / "knowledge" / f"n{i}.md").write_text(f"# N{i}\nbody\n", encoding="utf-8")
    asyncio.run(_seed_legacy([("inbox", f"{_LEGACY_VAULT}/inbox/n.md")]))
    synced = runner.invoke(main_app, ["sync", str(vault)])
    assert synced.exit_code == 0, synced.output
    before = _rows()

    result = runner.invoke(
        main_app,
        ["index-prune-legacy", "vault", "--legacy-vault-root", _LEGACY_VAULT, "--apply"],
    )

    assert result.exit_code != 0
    assert "HEAD" in result.output
    assert _rows() == before


def test_prune_is_refused_after_a_sync_that_did_not_finish(
    alpha: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mixed_index(alpha)
    (alpha / "a.md").write_text("# A\nchanged\n", encoding="utf-8")
    broken = MagicMock()
    broken.embed.side_effect = RuntimeError("provider down")
    monkeypatch.setattr("axon.embedder.engine.EmbedderEngine", lambda: broken)
    failed = runner.invoke(main_app, ["sync", str(alpha)])
    assert isinstance(failed.exception, RuntimeError), failed.output
    before = _rows()

    result = _prune("--apply")

    assert result.exit_code != 0
    assert _rows() == before


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
    before = _rows()

    result = _prune("--apply")

    assert result.exit_code != 0
    assert _rows() == before


def test_prune_is_refused_for_a_repo_with_no_root_here(alpha: Path) -> None:
    _mixed_index(alpha)
    before = _rows()

    result = runner.invoke(
        main_app,
        ["index-prune-legacy", "beta", "--legacy-vault-root", _LEGACY_VAULT, "--apply"],
    )

    assert result.exit_code != 0
    assert _rows() == before
