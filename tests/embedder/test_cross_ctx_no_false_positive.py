"""Prove indexing ctx 'work' never deletes entries scoped to 'knowledge'.

Uses mock store + real SqliteFileCache (tmp sqlite) preloaded with a
'knowledge' entry. After indexing a 'work' path, assert that:
- delete_by_file is never called with ctx='knowledge'
- the knowledge cache entry survives (list_entries('knowledge') is non-empty)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.embedder.engine import default_embedding_dimension  # noqa: E402
from axon.store.pg_file_cache import PostgresFileCache  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def _make_real_cache_with_knowledge(dsn: str, knowledge_path: str):
    cache = PostgresFileCache(dsn=dsn)
    await cache.ensure_schema()
    await cache.set_entry(knowledge_path, "knowledge", "abc123", 1, status="done")
    return cache, cache


@pytest.mark.asyncio
async def test_indexing_work_does_not_delete_knowledge_entries(
    tmp_path: Path, pg_dsn
) -> None:
    """Indexing forced_ctx='work' must never call delete_by_file for ctx='knowledge'."""
    from axon.embedder.chunker import Chunk
    from axon.embedder.pipeline import index_path

    # Create a work file on disk
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    work_file = work_dir / "secret.py"
    work_file.write_text("def secret(): pass\n", encoding="utf-8")

    # The knowledge file does NOT exist on disk - it is only in the cache
    knowledge_posix = (tmp_path / "knowledge" / "notes.py").as_posix()

    file_cache, closer = await _make_real_cache_with_knowledge(pg_dsn, knowledge_posix)

    try:
        chunk_work = Chunk(
            symbol="secret",
            chunk_type="function",
            start_line=1,
            end_line=1,
            content="def secret(): pass",
            file_path=str(work_file),
            language="python",
        )

        store = AsyncMock()
        store.upsert_batch = AsyncMock()
        store.delete_by_file = AsyncMock()

        engine = MagicMock()
        engine.embed = MagicMock(
            side_effect=lambda texts: [[0.2] * default_embedding_dimension() for _ in texts]
        )

        with patch("axon.embedder.pipeline.chunk_source", return_value=[chunk_work]):
            await index_path(
                work_dir,
                engine=engine,
                store=store,
                vault_root=tmp_path,
                file_cache=file_cache,
                forced_ctx="work",
            )

        # Assert delete_by_file was NEVER called with ctx='knowledge'
        for c in store.delete_by_file.call_args_list:
            ctx_arg = c.args[0] if c.args else c.kwargs.get("ctx", "")
            assert ctx_arg != "knowledge", (
                f"delete_by_file must NOT be called with ctx='knowledge', got call: {c}"
            )

        # Assert the knowledge cache entry still exists
        knowledge_entries = await file_cache.list_entries("knowledge")
        assert len(knowledge_entries) >= 1, (
            "Knowledge cache entry must survive indexing of 'work' context"
        )
        knowledge_paths = [e[0] for e in knowledge_entries]
        assert knowledge_posix in knowledge_paths, (
            f"Expected {knowledge_posix} in knowledge entries, got {knowledge_paths}"
        )

    finally:
        await closer.close()
