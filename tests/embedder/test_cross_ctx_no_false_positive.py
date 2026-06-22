"""Prove indexing ctx 'work' never deletes entries scoped to 'knowledge'.

Uses mock store + real SqliteFileCache (tmp sqlite) preloaded with a
'knowledge' entry. After indexing a 'work' path, assert that:
- delete_by_file is never called with ctx='knowledge'
- the knowledge cache entry survives (list_entries('knowledge') is non-empty)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import aiosqlite

from axon.store.file_cache import SqliteFileCache


async def _make_real_cache_with_knowledge(
    db_path: str, knowledge_path: str
) -> tuple[SqliteFileCache, object]:
    conn = await aiosqlite.connect(db_path)
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS file_index (
            file_path TEXT NOT NULL,
            ctx TEXT NOT NULL,
            sha1 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'done',
            chunk_count INTEGER NOT NULL DEFAULT 0,
            indexed_at TEXT NOT NULL,
            PRIMARY KEY (file_path, ctx)
        )
        """
    )
    # Pre-seed a 'knowledge' entry
    await conn.execute(
        """
        INSERT INTO file_index (file_path, ctx, sha1, status, chunk_count, indexed_at)
        VALUES (?, 'knowledge', 'abc123', 'done', 1, '2026-01-01T00:00:00+00:00')
        """,
        (knowledge_path,),
    )
    await conn.commit()
    lock = asyncio.Lock()
    return SqliteFileCache(conn, lock), conn


@pytest.mark.asyncio
async def test_indexing_work_does_not_delete_knowledge_entries(tmp_path: Path) -> None:
    """Indexing forced_ctx='work' must never call delete_by_file for ctx='knowledge'."""
    from axon.embedder.pipeline import index_path
    from axon.embedder.chunker import Chunk

    # Create a work file on disk
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    work_file = work_dir / "secret.py"
    work_file.write_text("def secret(): pass\n", encoding="utf-8")

    # The knowledge file does NOT exist on disk - it is only in the cache
    knowledge_posix = (tmp_path / "knowledge" / "notes.py").as_posix()

    db_path = str(tmp_path / "test_cache.db")
    file_cache, conn = await _make_real_cache_with_knowledge(db_path, knowledge_posix)

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
        engine.embed = MagicMock(side_effect=lambda texts: [[0.2] * 768 for _ in texts])

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
        await conn.close()
