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
                    indexed_at  timestamptz NOT NULL,
                    chunker_version text,
                    repo        text    NOT NULL DEFAULT '',
                    CONSTRAINT file_index_repo_pkey PRIMARY KEY (repo, file_path, ctx)
                )
                """
            )
            # Existing databases predate the column; legacy rows keep NULL,
            # which reads as stale and reindexes once.
            await con.execute(
                "ALTER TABLE file_index ADD COLUMN IF NOT EXISTS chunker_version text"
            )
            # Retrofit repo column and move PK to (repo, file_path, ctx) for
            # pre-existing databases (matching 0007_identity_and_kind.sql). A
            # PRIMARY KEY inline in CREATE TABLE IF NOT EXISTS never retrofits
            # an existing table, and CLI index commands call ensure_schema
            # directly without running migrations.
            await con.execute(
                "ALTER TABLE file_index ADD COLUMN IF NOT EXISTS repo text NOT NULL DEFAULT ''"
            )
            await con.execute(
                """
                DO $$
                DECLARE
                    existing_pk text;
                BEGIN
                    IF to_regclass('file_index') IS NULL THEN
                        RETURN;
                    END IF;
                    SELECT conname INTO existing_pk
                      FROM pg_constraint
                     WHERE conrelid = 'file_index'::regclass AND contype = 'p';
                    IF existing_pk = 'file_index_repo_pkey' THEN
                        RETURN;
                    END IF;
                    IF existing_pk IS NOT NULL THEN
                        EXECUTE format('ALTER TABLE file_index DROP CONSTRAINT %I', existing_pk);
                    END IF;
                    ALTER TABLE file_index
                        ADD CONSTRAINT file_index_repo_pkey PRIMARY KEY (repo, file_path, ctx);
                END $$;
                """
            )
            await con.execute(
                "CREATE INDEX IF NOT EXISTS ix_file_index_ctx ON file_index (ctx)"
            )
            await con.execute(
                "CREATE INDEX IF NOT EXISTS ix_file_index_status ON file_index (status)"
            )

    async def get_all_sha1s(
        self, ctx: str, *, chunker_version: str | None = None, repo: str = ""
    ) -> dict[str, str]:
        """Cached sha1s for ctx, excluding anything a different chunker produced.

        A row whose chunker_version does not match is simply not reported, so
        the caller reindexes it without needing to know why. NULL - a row
        written before this column existed - never matches, so legacy entries
        reindex once.
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            if chunker_version is None:
                rows = await con.fetch(
                    "SELECT file_path, sha1 FROM file_index"
                    " WHERE ctx=$1 AND status='done' AND repo=$2",
                    ctx,
                    repo,
                )
            else:
                rows = await con.fetch(
                    "SELECT file_path, sha1 FROM file_index"
                    " WHERE ctx=$1 AND status='done' AND chunker_version=$2 AND repo=$3",
                    ctx,
                    chunker_version,
                    repo,
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
        chunker_version: str | None = None,
        repo: str = "",
    ) -> None:
        fp = Path(file_path.replace("\\", "/")).as_posix()
        now = datetime.now(UTC)
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO file_index
                    (file_path, ctx, sha1, status, chunk_count, indexed_at,
                     chunker_version, repo)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (repo, file_path, ctx) DO UPDATE SET
                    sha1            = excluded.sha1,
                    status          = excluded.status,
                    chunk_count     = excluded.chunk_count,
                    indexed_at      = excluded.indexed_at,
                    chunker_version = excluded.chunker_version
                """,
                fp,
                ctx,
                sha1,
                status,
                chunk_count,
                now,
                chunker_version,
                repo,
            )

    async def delete_entry(self, file_path: str, ctx: str, *, repo: str = "") -> None:
        fp = Path(file_path.replace("\\", "/")).as_posix()
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                "DELETE FROM file_index WHERE file_path=$1 AND ctx=$2 AND repo=$3",
                fp,
                ctx,
                repo,
            )

    async def list_entries(self, ctx: str, *, repo: str = "") -> list[tuple[str, str]]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT file_path, sha1 FROM file_index WHERE ctx=$1 AND repo=$2",
                ctx,
                repo,
            )
        return [(r["file_path"], r["sha1"]) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
