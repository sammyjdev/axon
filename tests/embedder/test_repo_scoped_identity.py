"""Tests for repo-scoped file identity and kinds in embedder pipeline."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from axon.core.file_identity import RepoRoots, build_repo_roots
from axon.core.repo_identity import repo_identity
from axon.embedder.pipeline import index_path, ingest_file
from axon.store.file_cache import sha1_of_source
from axon.store.pg_file_cache import PostgresFileCache
from axon.store.pg_vector_store import VECTOR_SIZE, PgVectorStore
from axon.store.vector_common import Chunk as VectorChunk


def _get_test_pg_url() -> str:
    env = os.environ.get("AXON_PG_URL")
    if env:
        return env
    from axon.config.runtime import load_runtime_config

    return load_runtime_config().pg_url


def _mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.side_effect = lambda texts: [[0.0] * VECTOR_SIZE for _ in texts]
    return engine


def _two_repos(tmp_path: Path) -> tuple[Path, Path, RepoRoots]:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir(parents=True, exist_ok=True)
    repo_b.mkdir(parents=True, exist_ok=True)
    (repo_a / "README.md").write_text("# Section A\ncontent a\n", encoding="utf-8")
    (repo_b / "README.md").write_text("# Section B\ncontent b\n", encoding="utf-8")
    roots = build_repo_roots([repo_a, repo_b], tmp_path / "vault")
    return repo_a, repo_b, roots


@pytest.mark.asyncio
async def test_two_repos_keep_their_own_chunks_for_the_same_relative_path(
    tmp_path: Path,
) -> None:
    """Two repos with the same relative path keep distinct chunks across reindexes."""
    repo_a, repo_b, roots = _two_repos(tmp_path)
    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()
    cache = PostgresFileCache(_get_test_pg_url())
    await cache.ensure_schema()
    engine = _mock_engine()

    try:
        await index_path(
            repo_a,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
        )
        await index_path(
            repo_b,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
        )

        pool = await store._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, project, file_path, ctx FROM embeddings ORDER BY project"
            )
        assert len(rows) == 2
        row_a = rows[0]
        row_b = rows[1]
        assert row_a["project"] == "repo_a"
        assert row_a["file_path"] == "README.md"
        assert row_a["ctx"] == "knowledge"
        assert row_b["project"] == "repo_b"
        assert row_b["file_path"] == "README.md"
        assert row_b["ctx"] == "knowledge"
        assert row_a["id"] != row_b["id"]

        orig_b_tuple = (row_b["id"], row_b["project"], row_b["file_path"], row_b["ctx"])
        orig_a_id = row_a["id"]

        # Rewrite repo_a's file and reindex
        (repo_a / "README.md").write_text(
            "# Section A Rewritten\ncontent a rewritten\n", encoding="utf-8"
        )
        await index_path(
            repo_a,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
        )

        async with pool.acquire() as con:
            new_rows = await con.fetch(
                "SELECT id, project, file_path, ctx FROM embeddings ORDER BY project"
            )
        assert len(new_rows) == 2
        new_row_a = new_rows[0]
        new_row_b = new_rows[1]

        # Assert surviving rows by exact (id, project, file_path, ctx) identity
        assert (
            new_row_b["id"],
            new_row_b["project"],
            new_row_b["file_path"],
            new_row_b["ctx"],
        ) == orig_b_tuple
        assert new_row_a["id"] != orig_a_id
        assert new_row_a["project"] == "repo_a"
        assert new_row_a["file_path"] == "README.md"
        assert new_row_a["ctx"] == "knowledge"
    finally:
        await store.close()
        await cache.close()


@pytest.mark.asyncio
async def test_reindexing_one_repo_leaves_the_other_cache_entry_untouched(
    tmp_path: Path,
) -> None:
    """Reindexing repo_a leaves repo_b's sha1 cache entry intact."""
    repo_a, repo_b, roots = _two_repos(tmp_path)
    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()
    cache = PostgresFileCache(_get_test_pg_url())
    await cache.ensure_schema()
    engine = _mock_engine()

    try:
        await index_path(
            repo_a,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
        )
        await index_path(
            repo_b,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
        )

        b_sha1 = sha1_of_source("# Section B\ncontent b\n")
        b_entries_before = await cache.list_entries("knowledge", repo="repo_b")
        assert b_entries_before == [("README.md", b_sha1)]

        # Rewrite repo_a and reindex
        (repo_a / "README.md").write_text(
            "# Section A Rewritten\ncontent a rewritten\n", encoding="utf-8"
        )
        await index_path(
            repo_a,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
        )

        b_entries_after = await cache.list_entries("knowledge", repo="repo_b")
        assert b_entries_after == [("README.md", b_sha1)]
    finally:
        await store.close()
        await cache.close()


@pytest.mark.asyncio
async def test_a_delete_in_one_repo_spares_the_same_path_in_another_ctx(
    tmp_path: Path,
) -> None:
    """Reindexing repo_a in knowledge ctx spares repo_b's row in another ctx."""
    repo_a, repo_b, roots = _two_repos(tmp_path)
    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()
    cache = PostgresFileCache(_get_test_pg_url())
    await cache.ensure_schema()
    engine = _mock_engine()

    try:
        # Seed repo_b under personal ctx
        (repo_b / "README.md").write_text("# Personal B\ncontent b\n", encoding="utf-8")
        await index_path(
            repo_b,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
            forced_ctx="personal",
        )
        # Seed repo_b under knowledge ctx
        (repo_b / "README.md").write_text("# Knowledge B\ncontent b\n", encoding="utf-8")
        await index_path(
            repo_b,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
            forced_ctx="knowledge",
        )
        # Seed repo_a under knowledge ctx
        await index_path(
            repo_a,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
            forced_ctx="knowledge",
        )

        # Reindex repo_a under knowledge ctx
        (repo_a / "README.md").write_text("# Section A Changed\nchanged a\n", encoding="utf-8")
        await index_path(
            repo_a,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
            forced_ctx="knowledge",
        )

        pool = await store._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT project, file_path, ctx FROM embeddings WHERE project='repo_b'"
            )
        surviving = {(r["project"], r["file_path"], r["ctx"]) for r in rows}
        assert surviving == {
            ("repo_b", "README.md", "personal"),
            ("repo_b", "README.md", "knowledge"),
        }
    finally:
        await store.close()
        await cache.close()


@pytest.mark.asyncio
async def test_the_same_file_under_two_absolute_roots_gets_the_same_ids(
    tmp_path: Path,
) -> None:
    """The same file indexed under two different machine roots produces identical chunk IDs."""
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir(parents=True, exist_ok=True)
    (repo_a / "README.md").write_text("identical content\n", encoding="utf-8")

    roots_1 = build_repo_roots([repo_a], tmp_path / "vault")
    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()
    cache = PostgresFileCache(_get_test_pg_url())
    await cache.ensure_schema()
    engine = _mock_engine()

    try:
        await index_path(
            repo_a,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots_1,
        )

        pool = await store._ensure_pool()
        async with pool.acquire() as con:
            rows_1 = await con.fetch("SELECT id FROM embeddings WHERE project='repo_a'")
        ids_1 = {r["id"] for r in rows_1}
        assert len(ids_1) >= 1

        # Mirror repo_a to a new path
        mirror_repo_a = tmp_path / "mirror" / "repo_a"
        mirror_repo_a.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(repo_a, mirror_repo_a)

        roots_2 = build_repo_roots([mirror_repo_a], tmp_path / "vault")
        await index_path(
            mirror_repo_a,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots_2,
        )

        async with pool.acquire() as con:
            rows_2 = await con.fetch("SELECT id FROM embeddings WHERE project='repo_a'")
        ids_2 = {r["id"] for r in rows_2}
        assert ids_1 == ids_2
        assert len(rows_2) == len(rows_1)
    finally:
        await store.close()
        await cache.close()


@pytest.mark.asyncio
async def test_a_legacy_cache_entry_is_never_a_hit_for_a_new_identity_file(
    tmp_path: Path,
) -> None:
    """A legacy cache entry with repo='' does not prevent indexing under a known root."""
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir(parents=True, exist_ok=True)
    readme = repo_a / "README.md"
    readme.write_text("sample content\n", encoding="utf-8")
    current_sha1 = sha1_of_source("sample content\n")
    abs_posix = readme.as_posix()

    roots = build_repo_roots([repo_a], tmp_path / "vault")
    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()
    cache = PostgresFileCache(_get_test_pg_url())
    await cache.ensure_schema()
    engine = _mock_engine()

    try:
        # Pre-insert a legacy file_index row with repo='' and current sha1
        await cache.set_entry(abs_posix, "knowledge", current_sha1, 1, repo="")

        indexed_files, total_chunks = await index_path(
            repo_a,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
        )
        assert indexed_files == 1
        assert total_chunks >= 1

        # Both entries exist: the new scoped row and the untouched legacy row
        new_entries = await cache.list_entries("knowledge", repo="repo_a")
        assert new_entries == [("README.md", current_sha1)]

        legacy_entries = await cache.list_entries("knowledge", repo="")
        assert (abs_posix, current_sha1) in legacy_entries
    finally:
        await store.close()
        await cache.close()


@pytest.mark.asyncio
async def test_a_file_under_no_known_root_keeps_todays_shape(
    tmp_path: Path,
) -> None:
    """Loose files under no known root keep legacy absolute path and kind is None."""
    loose_dir = tmp_path / "loose"
    loose_dir.mkdir(parents=True, exist_ok=True)
    loose_file = loose_dir / "x.py"
    loose_file.write_text("def hello(): pass\n", encoding="utf-8")

    class _StrictNullCache:
        async def get_all_sha1s(
            self, ctx: str, *, chunker_version: str | None = None
        ) -> dict[str, str]:
            return {}

        async def set_entry(
            self,
            fp: str,
            ctx: str,
            sha1: str,
            cc: int,
            *,
            status: str = "done",
            chunker_version: str | None = None,
        ) -> None:
            pass

        async def delete_entry(self, fp: str, ctx: str) -> None:
            pass

        async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
            return []

    class _StrictFakeStore:
        def __init__(self) -> None:
            self.batches: list[list[VectorChunk]] = []

        async def upsert_batch(self, chunks: list[VectorChunk]) -> None:
            self.batches.append(list(chunks))

        async def delete_by_file(self, ctx: str, file_path: str) -> None:
            pass

    store = _StrictFakeStore()
    cache = _StrictNullCache()
    engine = _mock_engine()

    indexed_files, total_chunks = await index_path(
        loose_dir,
        engine=engine,
        store=store,
        vault_root=tmp_path / "vault",
        file_cache=cache,
        repo_roots={},
    )
    assert indexed_files == 1
    assert total_chunks >= 1
    assert len(store.batches) >= 1

    stored_chunk = store.batches[0][0]
    assert stored_chunk.file_path == loose_file.as_posix()
    assert stored_chunk.project == repo_identity(loose_dir)
    assert stored_chunk.kind is None


@pytest.mark.asyncio
async def test_the_vault_is_one_identity_with_the_folder_inside_the_path(
    tmp_path: Path,
) -> None:
    """Vault files are identified with project='vault' and relative path including folder."""
    vault_dir = tmp_path / "vault"
    dec_file = vault_dir / "Decisions" / "dec-1.md"
    dec_file.parent.mkdir(parents=True, exist_ok=True)
    dec_file.write_text("# Decision 1\nContent\n", encoding="utf-8")

    roots = build_repo_roots([], vault_dir)
    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()
    cache = PostgresFileCache(_get_test_pg_url())
    await cache.ensure_schema()
    engine = _mock_engine()

    try:
        await index_path(
            vault_dir,
            engine=engine,
            store=store,
            vault_root=vault_dir,
            file_cache=cache,
            repo_roots=roots,
        )

        pool = await store._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch("SELECT project, file_path, kind FROM embeddings")
        assert len(rows) >= 1
        row = rows[0]
        assert row["project"] == "vault"
        assert row["file_path"] == "Decisions/dec-1.md"
        assert row["kind"] == "adr"
    finally:
        await store.close()
        await cache.close()


@pytest.mark.asyncio
async def test_the_written_kind_comes_from_the_relative_path(
    tmp_path: Path,
) -> None:
    """Kinds are classified from relative paths and persisted in embeddings."""
    repo = tmp_path / "my_repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "docs").mkdir(parents=True, exist_ok=True)

    (repo / "src" / "x.py").write_text("def x(): pass\n", encoding="utf-8")
    (repo / "README.md").write_text("# Readme\n", encoding="utf-8")
    (repo / "docs" / "ADR.md").write_text("# ADR\n", encoding="utf-8")

    roots = build_repo_roots([repo], tmp_path / "vault")
    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()
    cache = PostgresFileCache(_get_test_pg_url())
    await cache.ensure_schema()
    engine = _mock_engine()

    try:
        await index_path(
            repo,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
        )

        pool = await store._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch("SELECT file_path, kind FROM embeddings")
        kinds = {(r["file_path"], r["kind"]) for r in rows}
        assert kinds == {
            ("src/x.py", "code"),
            ("README.md", "doc"),
            ("docs/ADR.md", "adr"),
        }
    finally:
        await store.close()
        await cache.close()


@pytest.mark.asyncio
async def test_ingest_file_writes_the_identity_and_the_kind(
    tmp_path: Path,
) -> None:
    """ingest_file writes repo-scoped identity and kind."""
    repo = tmp_path / "repo_ingest"
    docs_dir = repo / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    adr_file = docs_dir / "ADR.md"
    adr_file.write_text("# ADR ingest\n", encoding="utf-8")

    roots = build_repo_roots([repo], tmp_path / "vault")
    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()
    engine = _mock_engine()

    try:
        count = await ingest_file(adr_file, engine=engine, store=store, repo_roots=roots)
        assert count >= 1

        pool = await store._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch("SELECT project, file_path, kind FROM embeddings")
        assert len(rows) >= 1
        assert (rows[0]["project"], rows[0]["file_path"], rows[0]["kind"]) == (
            "repo_ingest",
            "docs/ADR.md",
            "adr",
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_files_under_no_known_root_are_reported_once_per_run(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Files under no known root trigger exactly one warning per run."""
    loose_dir = tmp_path / "loose"
    loose_dir.mkdir(parents=True, exist_ok=True)
    (loose_dir / "x.py").write_text("def x(): pass\n", encoding="utf-8")
    (loose_dir / "y.py").write_text("def y(): pass\n", encoding="utf-8")

    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()
    cache = PostgresFileCache(_get_test_pg_url())
    await cache.ensure_schema()
    engine = _mock_engine()

    try:
        with caplog.at_level(logging.WARNING, logger="axon.embedder.pipeline"):
            await index_path(
                loose_dir,
                engine=engine,
                store=store,
                vault_root=tmp_path / "vault",
                file_cache=cache,
                repo_roots={},
            )

        warn_records = [
            r
            for r in caplog.records
            if r.name == "axon.embedder.pipeline"
            and r.levelno == logging.WARNING
            and "under no known repo root" in r.getMessage()
        ]
        assert len(warn_records) == 1
        assert "2 file(s)" in warn_records[0].getMessage()

        # Run where all files have identity produces zero warning records
        caplog.clear()
        repo = tmp_path / "repo_identified"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "z.py").write_text("def z(): pass\n", encoding="utf-8")
        roots = build_repo_roots([repo], tmp_path / "vault")

        with caplog.at_level(logging.WARNING, logger="axon.embedder.pipeline"):
            await index_path(
                repo,
                engine=engine,
                store=store,
                vault_root=tmp_path / "vault",
                file_cache=cache,
                repo_roots=roots,
            )

        new_warn_records = [
            r
            for r in caplog.records
            if r.name == "axon.embedder.pipeline"
            and r.levelno == logging.WARNING
            and "under no known repo root" in r.getMessage()
        ]
        assert len(new_warn_records) == 0
    finally:
        await store.close()
        await cache.close()


@pytest.mark.asyncio
async def test_a_mixed_walk_never_deletes_the_legacy_row_of_an_identified_file(
    tmp_path: Path,
) -> None:
    """A mixed walk skips legacy rows of files that now have identities."""
    workspace = tmp_path / "workspace"
    repo_a = workspace / "repo_a"
    loose = workspace / "loose"
    repo_a.mkdir(parents=True, exist_ok=True)
    loose.mkdir(parents=True, exist_ok=True)

    readme = repo_a / "README.md"
    readme.write_text("repo a readme\n", encoding="utf-8")
    loose_py = loose / "x.py"
    loose_py.write_text("def loose(): pass\n", encoding="utf-8")

    abs_readme = readme.as_posix()
    roots = build_repo_roots([repo_a], tmp_path / "vault")

    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()
    cache = PostgresFileCache(_get_test_pg_url())
    await cache.ensure_schema()
    engine = _mock_engine()

    try:
        # Pre-insert legacy file_index and embeddings rows for repo_a/README.md
        await cache.set_entry(abs_readme, "knowledge", "old_sha1_val", 1, repo="")
        legacy_chunk = VectorChunk(
            id="legacy-chunk-id",
            vector=[0.0] * VECTOR_SIZE,
            file_path=abs_readme,
            language="markdown",
            chunk_type="file",
            symbol="root",
            project="repo_a",
            ctx="knowledge",
            content="repo a readme\n",
        )
        await store.upsert_batch([legacy_chunk])

        # Index the whole workspace (which contains both repo_a and loose)
        await index_path(
            workspace,
            engine=engine,
            store=store,
            vault_root=tmp_path / "vault",
            file_cache=cache,
            repo_roots=roots,
        )

        # Legacy file_index row must still exist
        pool = await store._ensure_pool()
        async with pool.acquire() as con:
            legacy_idx = await con.fetch(
                "SELECT file_path, repo FROM file_index WHERE repo='' AND file_path=$1",
                abs_readme,
            )
            assert len(legacy_idx) == 1

            # Legacy embeddings row must still exist
            legacy_emb = await con.fetch(
                "SELECT id, file_path, project FROM embeddings WHERE id='legacy-chunk-id'"
            )
            assert len(legacy_emb) == 1

            # New scoped rows must also exist
            new_idx = await con.fetch(
                "SELECT file_path, repo FROM file_index "
                "WHERE repo='repo_a' AND file_path='README.md'"
            )
            assert len(new_idx) == 1

            new_emb = await con.fetch(
                "SELECT id, file_path, project FROM embeddings "
                "WHERE project='repo_a' AND file_path='README.md'"
            )
            assert len(new_emb) >= 1
    finally:
        await store.close()
        await cache.close()
