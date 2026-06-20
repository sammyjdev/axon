"""Integration: SessionStore.make_file_cache() wires SqliteFileCache to the
real DB (migrations applied by init()), sharing the store's connection+lock."""

from __future__ import annotations

from pathlib import Path

import pytest


async def test_make_file_cache_round_trips(tmp_path: Path) -> None:
    from axon.store.session_store import SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    cache = store.make_file_cache()
    try:
        await cache.set_entry("src/foo.py", "personal", "abc123", 3)
        result = await cache.get_all_sha1s("personal")
        assert result == {"src/foo.py": "abc123"}
    finally:
        await store.close()


async def test_make_file_cache_pending_invisible(tmp_path: Path) -> None:
    from axon.store.session_store import SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    cache = store.make_file_cache()
    try:
        await cache.set_entry("src/foo.py", "personal", "abc", 5, status="pending")
        assert await cache.get_all_sha1s("personal") == {}
    finally:
        await store.close()


async def test_make_file_cache_before_init_raises(tmp_path: Path) -> None:
    from axon.store.session_store import SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    with pytest.raises(RuntimeError, match="init"):
        store.make_file_cache()
