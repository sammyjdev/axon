"""Postgres-backed FileCache (dec-121 step 3, wave 1).

Implements the same FileCache Protocol surface as SqliteFileCache, byte-for-byte:
status='done' filter in get_all_sha1s, posix path normalization, ON CONFLICT
upsert, list_entries returning all statuses. Own asyncpg pool; no shared lock.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import asyncpg


class PostgresFileCache:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        return self._pool

    async def ensure_schema(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS file_index (
                    file_path   text    NOT NULL,
                    ctx         text    NOT NULL,
                    sha1        text    NOT NULL,
                    status      text    NOT NULL DEFAULT 'done',
                    chunk_count integer NOT NULL DEFAULT 0,
                    indexed_at  text    NOT NULL,
                    PRIMARY KEY (file_path, ctx)
                )
                """
            )
            await con.execute(
                "CREATE INDEX IF NOT EXISTS ix_file_index_ctx ON file_index (ctx)"
            )
            await con.execute(
                "CREATE INDEX IF NOT EXISTS ix_file_index_status ON file_index (status)"
            )

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT file_path, sha1 FROM file_index WHERE ctx=$1 AND status='done'",
                ctx,
            )
        return {r["file_path"]: r["sha1"] for r in rows}

    async def set_entry(
        self,
        file_path: str,
        ctx: str,
        sha1: str,
        chunk_count: int,
        *,
        status: str = "done",
    ) -> None:
        fp = Path(file_path).as_posix()
        now = datetime.now(UTC).isoformat()
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO file_index
                    (file_path, ctx, sha1, status, chunk_count, indexed_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (file_path, ctx) DO UPDATE SET
                    sha1        = excluded.sha1,
                    status      = excluded.status,
                    chunk_count = excluded.chunk_count,
                    indexed_at  = excluded.indexed_at
                """,
                fp, ctx, sha1, status, chunk_count, now,
            )

    async def delete_entry(self, file_path: str, ctx: str) -> None:
        fp = Path(file_path).as_posix()
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                "DELETE FROM file_index WHERE file_path=$1 AND ctx=$2", fp, ctx
            )

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT file_path, sha1 FROM file_index WHERE ctx=$1", ctx
            )
        return [(r["file_path"], r["sha1"]) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
