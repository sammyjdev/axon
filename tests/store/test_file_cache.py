# tests/store/test_file_cache.py
"""Unit tests for SqliteFileCache - all run against a real in-memory SQLite DB."""
from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from axon.store.file_cache import SqliteFileCache, sha1_of_source


async def _make_cache(tmp_path: Path) -> tuple[SqliteFileCache, aiosqlite.Connection]:
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(db_path)
    await conn.execute("""
        CREATE TABLE file_index (
            file_path TEXT NOT NULL,
            ctx TEXT NOT NULL,
            sha1 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'done',
            chunk_count INTEGER NOT NULL DEFAULT 0,
            indexed_at TEXT NOT NULL,
            PRIMARY KEY (file_path, ctx)
        )
    """)
    await conn.commit()
    lock = asyncio.Lock()
    cache = SqliteFileCache(conn, lock)
    return cache, conn


async def test_get_all_sha1s_empty(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    result = await cache.get_all_sha1s("personal")
    assert result == {}
    await conn.close()


async def test_get_all_sha1s_filters_pending(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    await cache.set_entry("src/foo.py", "personal", "abc123", 5, status="pending")
    result = await cache.get_all_sha1s("personal")
    assert result == {}  # pending rows MUST NOT appear
    await conn.close()


async def test_get_all_sha1s_returns_done(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    await cache.set_entry("src/foo.py", "personal", "abc123", 5, status="done")
    result = await cache.get_all_sha1s("personal")
    assert result == {"src/foo.py": "abc123"}
    await conn.close()


async def test_set_entry_upsert_updates_sha1(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    await cache.set_entry("src/foo.py", "personal", "aaa", 3)
    await cache.set_entry("src/foo.py", "personal", "bbb", 4)
    result = await cache.get_all_sha1s("personal")
    assert result["src/foo.py"] == "bbb"
    await conn.close()


async def test_set_entry_pending_then_done(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    await cache.set_entry("src/foo.py", "personal", "abc", 5, status="pending")
    assert await cache.get_all_sha1s("personal") == {}
    await cache.set_entry("src/foo.py", "personal", "abc", 5, status="done")
    assert await cache.get_all_sha1s("personal") == {"src/foo.py": "abc"}
    await conn.close()


async def test_delete_entry_removes_row(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    await cache.set_entry("src/foo.py", "personal", "abc", 5)
    await cache.delete_entry("src/foo.py", "personal")
    assert await cache.get_all_sha1s("personal") == {}
    await conn.close()


async def test_list_entries_filters_by_ctx(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    await cache.set_entry("src/foo.py", "personal", "aaa", 2)
    await cache.set_entry("src/bar.py", "knowledge", "bbb", 3)
    entries = await cache.list_entries("personal")
    paths = {e[0] for e in entries}
    assert "src/foo.py" in paths
    assert "src/bar.py" not in paths
    await conn.close()


async def test_path_normalization_backslash(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    # Simulate Windows path with backslashes
    await cache.set_entry("src\\foo\\bar.py", "personal", "abc", 2)
    result = await cache.get_all_sha1s("personal")
    # Must be stored and returned as posix
    assert "src/foo/bar.py" in result
    assert "src\\foo\\bar.py" not in result
    await conn.close()


def test_sha1_of_source_matches_pipeline_hash() -> None:
    source = "def hello():\n    return 42\n"
    import hashlib
    expected = hashlib.sha1(source.encode("utf-8")).hexdigest()
    assert sha1_of_source(source) == expected
