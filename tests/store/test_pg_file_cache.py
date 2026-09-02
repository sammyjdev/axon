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


async def test_a_stale_chunker_version_hides_the_entry(pg_dsn) -> None:
    """A chunker improvement must invalidate what the old chunker produced.

    The cache keys on the file's sha1, so a better chunker was invisible to
    every file that did not change afterwards: the fix shipped in the binary
    and the index kept the old output, with nothing signalling the drift.
    Measured 2026-09-01 on the real index - after dec-132 landed, a full
    `index-dev` run reprocessed 75 of 575 files and produced 78 of the 583
    expected class chunks, and only a manual DELETE on file_index recovered it.
    """
    from axon.store.pg_file_cache import PostgresFileCache

    cache = PostgresFileCache(dsn=pg_dsn)
    await cache.ensure_schema()
    # A distinct ctx per test: the container is module-scoped, so rows leak.
    await cache.set_entry("/r/a.py", "ctx-stale", "sha-1", 3, chunker_version="v1")

    assert await cache.get_all_sha1s("ctx-stale", chunker_version="v1") == {
        "/r/a.py": "sha-1"
    }
    assert await cache.get_all_sha1s("ctx-stale", chunker_version="v2") == {}


async def test_reindexing_under_a_new_version_refreshes_the_entry(pg_dsn) -> None:
    from axon.store.pg_file_cache import PostgresFileCache

    cache = PostgresFileCache(dsn=pg_dsn)
    await cache.ensure_schema()
    await cache.set_entry("/r/b.py", "ctx-refresh", "sha-1", 3, chunker_version="v1")
    await cache.set_entry("/r/b.py", "ctx-refresh", "sha-1", 5, chunker_version="v2")

    assert await cache.get_all_sha1s("ctx-refresh", chunker_version="v2") == {
        "/r/b.py": "sha-1"
    }
    assert await cache.get_all_sha1s("ctx-refresh", chunker_version="v1") == {}


async def test_rows_written_before_this_column_existed_are_treated_as_stale(
    pg_dsn,
) -> None:
    """Legacy rows carry no version. They must reindex once, not be trusted."""
    import asyncpg

    from axon.store.pg_file_cache import PostgresFileCache

    cache = PostgresFileCache(dsn=pg_dsn)
    await cache.ensure_schema()
    con = await asyncpg.connect(pg_dsn)
    await con.execute(
        "INSERT INTO file_index (file_path, ctx, sha1, status, chunk_count, indexed_at)"
        " VALUES ('/r/legacy.py', 'career', 'sha-9', 'done', 2, now())"
    )
    await con.close()

    assert await cache.get_all_sha1s("career", chunker_version="v1") == {}
