from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_set_then_get_excludes_pending(pg_dsn) -> None:
    from axon.store.pg_file_cache import PostgresFileCache

    cache = PostgresFileCache(dsn=pg_dsn)
    try:
        await cache.ensure_schema()
        await cache.ensure_schema()  # idempotent
        await cache.set_entry("a/b.py", "knowledge", "sha-done", 3)
        await cache.set_entry("a/c.py", "knowledge", "sha-pending", 0, status="pending")
        done = await cache.get_all_sha1s("knowledge")
        assert done == {"a/b.py": "sha-done"}  # pending excluded
        all_rows = dict(await cache.list_entries("knowledge"))
        assert set(all_rows) == {"a/b.py", "a/c.py"}  # list_entries shows all statuses
    finally:
        await cache.close()


async def test_set_is_idempotent_and_posix_normalized(pg_dsn) -> None:
    from axon.store.pg_file_cache import PostgresFileCache

    cache = PostgresFileCache(dsn=pg_dsn)
    try:
        await cache.ensure_schema()
        async with (await cache._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE file_index")
        # backslash and posix forms must collide on the same row
        await cache.set_entry("d\\e.py", "work", "sha-1", 1)
        await cache.set_entry("d/e.py", "work", "sha-2", 2)
        rows = await cache.list_entries("work")
        assert rows == [("d/e.py", "sha-2")]  # one row, updated in place
    finally:
        await cache.close()


async def test_delete_entry_removes_only_that_row(pg_dsn) -> None:
    from axon.store.pg_file_cache import PostgresFileCache

    cache = PostgresFileCache(dsn=pg_dsn)
    try:
        await cache.ensure_schema()
        async with (await cache._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE file_index")
        await cache.set_entry("x.py", "knowledge", "sx", 1)
        await cache.set_entry("y.py", "knowledge", "sy", 1)
        await cache.delete_entry("x.py", "knowledge")
        remaining = await cache.get_all_sha1s("knowledge")
        assert remaining == {"y.py": "sy"}
    finally:
        await cache.close()
