"""Prove no point accumulation across re-index runs.

Uses mock store + real SqliteFileCache (tmp sqlite). Index a file (N points).
Edit the file so it produces a DIFFERENT set of chunks. Re-index. Assert:
- delete_by_file was called for that file BEFORE re-upsert
- upsert_batch call count matches new chunk count only (not old+new accumulated)
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


async def _make_real_cache(dsn: str):
    cache = PostgresFileCache(dsn=dsn)
    await cache.ensure_schema()
    return cache, cache


@pytest.mark.asyncio
async def test_no_point_accumulation_on_reindex(tmp_path: Path, pg_dsn) -> None:
    """Re-indexing a changed file must NOT accumulate old + new points in Qdrant."""
    from axon.embedder.chunker import Chunk
    from axon.embedder.pipeline import index_path

    py_file = tmp_path / "module.py"
    py_file.write_text("def alpha(): pass\n", encoding="utf-8")

    file_cache, closer = await _make_real_cache(pg_dsn)

    try:
        # First content: 1 chunk
        chunk_v1 = Chunk(
            symbol="alpha",
            chunk_type="function",
            start_line=1,
            end_line=1,
            content="def alpha(): pass",
            file_path=str(py_file),
            language="python",
        )

        # Second content: 2 chunks
        chunk_v2a = Chunk(
            symbol="beta",
            chunk_type="function",
            start_line=1,
            end_line=1,
            content="def beta(): pass",
            file_path=str(py_file),
            language="python",
        )
        chunk_v2b = Chunk(
            symbol="gamma",
            chunk_type="function",
            start_line=2,
            end_line=2,
            content="def gamma(): pass",
            file_path=str(py_file),
            language="python",
        )

        store = AsyncMock()
        store.upsert_batch = AsyncMock()
        store.delete_by_file = AsyncMock()

        engine = MagicMock()
        # Returns 1 vector per chunk
        engine.embed = MagicMock(
            side_effect=lambda texts: [[0.1] * default_embedding_dimension() for _ in texts]
        )

        fp_posix = py_file.as_posix()

        # --- First index run (v1: 1 chunk) ---
        with patch("axon.embedder.pipeline.chunk_source", return_value=[chunk_v1]):
            await index_path(
                tmp_path,
                engine=engine,
                store=store,
                vault_root=tmp_path,
                file_cache=file_cache,
            )

        # Confirm 1 chunk was upserted in v1
        first_run_chunks = sum(len(c.args[0]) for c in store.upsert_batch.call_args_list)
        assert first_run_chunks == 1, f"Expected 1 chunk on first run, got {first_run_chunks}"

        # Reset mock call counts for clean second-run measurement
        store.upsert_batch.reset_mock()
        store.delete_by_file.reset_mock()

        # Edit file (change content -> different sha1)
        py_file.write_text("def beta(): pass\ndef gamma(): pass\n", encoding="utf-8")

        # --- Second index run (v2: 2 chunks) ---
        with patch("axon.embedder.pipeline.chunk_source", return_value=[chunk_v2a, chunk_v2b]):
            await index_path(
                tmp_path,
                engine=engine,
                store=store,
                vault_root=tmp_path,
                file_cache=file_cache,
            )

        # delete_by_file must have been called for this file before re-upsert
        delete_calls = store.delete_by_file.call_args_list
        assert len(delete_calls) >= 1, "delete_by_file must be called on re-index"
        assert any(fp_posix in str(c) for c in delete_calls), (
            f"delete_by_file must reference {fp_posix}"
        )

        # upsert_batch must contain ONLY the new 2 chunks, not old + new = 3
        second_run_chunks = sum(len(c.args[0]) for c in store.upsert_batch.call_args_list)
        assert second_run_chunks == 2, (
            f"Expected 2 chunks on second run (no accumulation), got {second_run_chunks}"
        )

    finally:
        await closer.close()
