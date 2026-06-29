# src/axon/store/file_cache.py
"""Persistent file-hash cache backed by the file_index SQLite table.

FileCache is a Protocol so tests can inject mocks.
SqliteFileCache is the production implementation - uses the aiosqlite
connection and asyncio.Lock already owned by SessionStore.

All file_path values are normalized to posix form before storage so that
Windows backslash paths and posix slash paths produce identical lookup keys.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from axon.config.runtime import RuntimeConfig


class FileCache(Protocol):
    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        """Return {file_path_posix: sha1} for all 'done' entries in ctx.

        Uses a single SELECT. Pending rows (crash sentinels) are excluded -
        they are treated as hash misses and trigger a full re-index.
        """
        ...

    async def set_entry(
        self,
        file_path: str,
        ctx: str,
        sha1: str,
        chunk_count: int,
        *,
        status: str = "done",
    ) -> None:
        """Insert or update a file_index row. Use status='pending' before
        vector-store mutation; status='done' only after _flush_batch() succeeds.
        """
        ...

    async def delete_entry(self, file_path: str, ctx: str) -> None:
        """Remove a file_index entry (used when file is deleted from repo)."""
        ...

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
        """Return [(file_path_posix, sha1)] for ALL entries in ctx (any status).

        Used to detect files removed from the repo (compare against walk result).
        """
        ...


class SqliteFileCache:
    """Production FileCache backed by an aiosqlite.Connection.

    Injected with the same conn and lock used by SessionStore to avoid
    opening a second connection (SQLite WAL allows multiple readers but
    serializes writers; sharing the lock prevents write contention from
    within the same process).
    """

    def __init__(self, conn: object, lock: object) -> None:
        # aiosqlite.Connection and asyncio.Lock - typed as object to avoid
        # importing aiosqlite at protocol definition time.
        self._conn = conn
        self._lock = lock

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT file_path, sha1 FROM file_index WHERE ctx=? AND status='done'",
                (ctx,),
            )
            rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}

    async def set_entry(
        self,
        file_path: str,
        ctx: str,
        sha1: str,
        chunk_count: int,
        *,
        status: str = "done",
    ) -> None:
        fp = Path(file_path.replace("\\", "/")).as_posix()
        now = datetime.now(UTC).isoformat()
        async with self._lock:
            await self._conn.execute(
                """
                INSERT INTO file_index
                    (file_path, ctx, sha1, status, chunk_count, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (file_path, ctx) DO UPDATE SET
                    sha1        = excluded.sha1,
                    status      = excluded.status,
                    chunk_count = excluded.chunk_count,
                    indexed_at  = excluded.indexed_at
                """,
                (fp, ctx, sha1, status, chunk_count, now),
            )
            await self._conn.commit()

    async def delete_entry(self, file_path: str, ctx: str) -> None:
        fp = Path(file_path.replace("\\", "/")).as_posix()
        async with self._lock:
            await self._conn.execute(
                "DELETE FROM file_index WHERE file_path=? AND ctx=?",
                (fp, ctx),
            )
            await self._conn.commit()

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT file_path, sha1 FROM file_index WHERE ctx=?",
                (ctx,),
            )
            return list(await cur.fetchall())


def sha1_of_source(source: str) -> str:
    """SHA-1 of UTF-8 encoded source content.

    MUST remain identical to pipeline.py:
        hashlib.sha1(source.encode("utf-8")).hexdigest()
    Any change here requires a matching change in pipeline.py AND
    a documented one-time cold-start full re-embed.

    Does not pass usedforsecurity kwarg to match pipeline.py exactly.
    """
    return hashlib.sha1(source.encode("utf-8")).hexdigest()


async def make_file_cache(runtime: RuntimeConfig) -> tuple[FileCache, object]:
    """Build the file-hash cache for the active ``fileindex_backend``.

    Returns ``(cache, closer)``; await ``closer.close()`` when finished (for the
    Postgres backend ``closer`` is the cache itself, which closes its pool).
    Honours the backend switch so callers never hard-wire SQLite.
    """
    if runtime.fileindex_backend == "postgres":
        from axon.store.pg_file_cache import PostgresFileCache

        cache = PostgresFileCache(dsn=runtime.pg_url)
        await cache.ensure_schema()
        return cache, cache

    import asyncio

    import aiosqlite

    from axon.store.session_store import _apply_migrations

    db_path = runtime.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_conn = await aiosqlite.connect(str(db_path))
    # Ensure the file_index table (003 migration) exists before first query.
    await _apply_migrations(db_conn)
    return SqliteFileCache(db_conn, asyncio.Lock()), db_conn
