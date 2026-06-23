# tests/cli/test_open_file_cache_backend.py
from __future__ import annotations

import dataclasses


async def test_open_file_cache_selects_postgres(monkeypatch) -> None:
    from axon.cli import pb

    constructed = {}

    class FakePgFileCache:
        def __init__(self, dsn: str) -> None:
            constructed["dsn"] = dsn

        async def ensure_schema(self) -> None:
            constructed["ensured"] = True

        async def close(self) -> None:
            constructed["closed"] = True

    monkeypatch.setattr("axon.store.pg_file_cache.PostgresFileCache", FakePgFileCache)
    monkeypatch.setattr(
        pb, "_RUNTIME", dataclasses.replace(pb._RUNTIME, fileindex_backend="postgres")
    )

    cache, handle = await pb._open_file_cache()
    assert isinstance(cache, FakePgFileCache)
    assert constructed["ensured"] is True
    await handle.close()
    assert constructed["closed"] is True


async def test_open_file_cache_defaults_to_sqlite(monkeypatch, tmp_path) -> None:
    from axon.cli import pb
    from axon.store.file_cache import SqliteFileCache

    monkeypatch.setattr(pb, "_get_db_path", lambda: tmp_path / "axon.db")
    monkeypatch.setattr(
        pb, "_RUNTIME", dataclasses.replace(pb._RUNTIME, fileindex_backend="sqlite")
    )
    cache, conn = await pb._open_file_cache()
    try:
        assert isinstance(cache, SqliteFileCache)
    finally:
        await conn.close()
