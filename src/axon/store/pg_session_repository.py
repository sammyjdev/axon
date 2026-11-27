"""Postgres-backed SessionRepository (dec-121 step 3, wave 4). Plain columns;
a session memory with a session id is upserted on it (#187); other memory and
note inserts use ON CONFLICT natural-key dedup (not bare RETURNING id);
code_change/session use ON CONFLICT PK upsert; no SQLite-lock fallback."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import asyncpg

from axon.store._session_columns import (
    SESSION_MEMORY_SELECT,
    row_to_code_change,
    row_to_session_memory,
    row_to_session_note,
)
from axon.store.pg_migrations import apply_pg_migrations
from axon.store.session_store import CodeChange, SessionMemory, SessionNote

_MEMORY_SELECT = f"SELECT {', '.join(SESSION_MEMORY_SELECT)} FROM session_memory"  # noqa: S608

# A keyed row keeps one snapshot per kind, each the greatest by its own data, so
# the stored row does not depend on the order the snapshots arrive in. Parameters:
# $1 project, $2 summary, $3 raw_turns, $4 created_at, $5 session_id. A row of
# another project is never updated: the statement then returns no id.
_UPSERT_COMPACT = """
INSERT INTO session_memory
    (project, summary, raw_turns, created_at, session_id,
     compact_summary, compact_created_at)
VALUES ($1, $2, $3, $4, $5, $2, $4)
ON CONFLICT (session_id) WHERE session_id IS NOT NULL DO UPDATE SET
    compact_summary = CASE
        WHEN session_memory.compact_created_at IS NULL
          OR (EXCLUDED.compact_created_at, octet_length(EXCLUDED.compact_summary),
              EXCLUDED.compact_summary COLLATE "C")
             > (session_memory.compact_created_at,
                octet_length(session_memory.compact_summary),
                session_memory.compact_summary COLLATE "C")
        THEN EXCLUDED.compact_summary ELSE session_memory.compact_summary END,
    compact_created_at = CASE
        WHEN session_memory.compact_created_at IS NULL
          OR (EXCLUDED.compact_created_at, octet_length(EXCLUDED.compact_summary),
              EXCLUDED.compact_summary COLLATE "C")
             > (session_memory.compact_created_at,
                octet_length(session_memory.compact_summary),
                session_memory.compact_summary COLLATE "C")
        THEN EXCLUDED.compact_created_at ELSE session_memory.compact_created_at END
WHERE session_memory.project = EXCLUDED.project
RETURNING id
"""

_NEWER_DIGEST = """
        session_memory.digest_created_at IS NULL
          OR (EXCLUDED.digest_created_at, octet_length(EXCLUDED.digest_summary),
              EXCLUDED.digest_summary COLLATE "C", EXCLUDED.digest_raw_turns)
             > (session_memory.digest_created_at,
                octet_length(session_memory.digest_summary),
                session_memory.digest_summary COLLATE "C",
                session_memory.digest_raw_turns)"""

_UPSERT_DIGEST = f"""
INSERT INTO session_memory
    (project, summary, raw_turns, created_at, session_id,
     digest_summary, digest_raw_turns, digest_created_at)
VALUES ($1, $2, $3, $4, $5, $2, $3, $4)
ON CONFLICT (session_id) WHERE session_id IS NOT NULL DO UPDATE SET
    digest_summary = CASE WHEN {_NEWER_DIGEST}
        THEN EXCLUDED.digest_summary ELSE session_memory.digest_summary END,
    digest_raw_turns = CASE WHEN {_NEWER_DIGEST}
        THEN EXCLUDED.digest_raw_turns ELSE session_memory.digest_raw_turns END,
    digest_created_at = CASE WHEN {_NEWER_DIGEST}
        THEN EXCLUDED.digest_created_at ELSE session_memory.digest_created_at END
WHERE session_memory.project = EXCLUDED.project
RETURNING id
"""  # noqa: S608 - only the constant condition above is interpolated

# The visible snapshot, read from the two kept ones alone: the digest when there
# is no compact summary, or when it is both later and at least as large.
_DIGEST_SHOWN = """(compact_created_at IS NULL OR (
        digest_created_at IS NOT NULL
        AND digest_created_at > compact_created_at
        AND octet_length(digest_summary) >= octet_length(compact_summary)))"""

_SHOW_PICK = f"""
UPDATE session_memory SET
    summary = CASE WHEN {_DIGEST_SHOWN} THEN digest_summary ELSE compact_summary END,
    raw_turns = CASE WHEN {_DIGEST_SHOWN} THEN digest_raw_turns ELSE 0 END,
    created_at = CASE WHEN {_DIGEST_SHOWN} THEN digest_created_at ELSE compact_created_at END
WHERE session_id = $1 AND project = $2
"""  # noqa: S608 - only the constant condition above is interpolated


class PostgresSessionRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        return self._pool

    async def ensure_schema(self) -> None:
        # Delegate to the versioned Postgres migration runner (MS-4 / #30). The
        # session baseline DDL is migration 0001; applying it is idempotent so
        # repeated calls and pre-existing tables are safe.
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await apply_pg_migrations(con)

    async def save_session_memory(self, mem: SessionMemory) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            if mem.session_id is not None:
                return await self._upsert_session_memory(con, mem)
            new_id = await con.fetchval(
                "INSERT INTO session_memory (project, summary, raw_turns, created_at)"
                " VALUES ($1, $2, $3, $4)"
                " ON CONFLICT (project, md5(summary), raw_turns, created_at)"
                " WHERE session_id IS NULL DO NOTHING"
                " RETURNING id",
                mem.project, mem.summary, mem.raw_turns, mem.created_at,
            )
            if new_id is None:
                new_id = await con.fetchval(
                    "SELECT id FROM session_memory"
                    " WHERE project=$1 AND summary=$2 AND raw_turns=$3 AND created_at=$4"
                    " AND session_id IS NULL",
                    mem.project, mem.summary, mem.raw_turns, mem.created_at,
                )
            return new_id

    async def _upsert_session_memory(
        self, con: asyncpg.Connection, mem: SessionMemory
    ) -> int:
        """One row per session id; a compact summary is a snapshot with no turns."""
        statement = _UPSERT_COMPACT if mem.raw_turns == 0 else _UPSERT_DIGEST
        async with con.transaction():
            row_id = await con.fetchval(
                statement,
                mem.project, mem.summary, mem.raw_turns, mem.created_at, mem.session_id,
            )
            if row_id is not None:
                await con.execute(_SHOW_PICK, mem.session_id, mem.project)
                return row_id
            # the session id belongs to another project's row, which stays as it is
            return await con.fetchval(
                "SELECT id FROM session_memory WHERE session_id=$1", mem.session_id
            )

    async def get_session_memories(self, project: str, limit: int = 3) -> list[SessionMemory]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                f"{_MEMORY_SELECT} WHERE project=$1"
                " ORDER BY created_at DESC, id DESC LIMIT $2",
                project, limit,
            )
        return [row_to_session_memory(r) for r in rows]

    async def duplicate_session_memories(self) -> list[tuple[str, int, list[int]]]:
        """(project, kept id, extra ids) per group of rows with no session id that
        share project, raw_turns and the exact summary text. Reads only: such a
        row says nothing reliable about which session wrote it (#187)."""
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT project, array_agg(id ORDER BY created_at DESC, id DESC) AS ids"
                " FROM session_memory WHERE session_id IS NULL"
                " GROUP BY project, raw_turns, summary HAVING count(*) > 1"
                " ORDER BY project, min(id)"
            )
        return [(r["project"], r["ids"][0], list(r["ids"][1:])) for r in rows]

    async def save_note(self, note: SessionNote) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            new_id = await con.fetchval(
                "INSERT INTO session_note (project, body, created_at)"
                " VALUES ($1, $2, $3)"
                " ON CONFLICT (project, md5(body), created_at) DO NOTHING"
                " RETURNING id",
                note.project, note.body, note.created_at,
            )
            if new_id is None:
                new_id = await con.fetchval(
                    "SELECT id FROM session_note"
                    " WHERE project=$1 AND body=$2 AND created_at=$3",
                    note.project, note.body, note.created_at,
                )
            return new_id

    async def get_notes(self, project: str, limit: int = 10) -> list[SessionNote]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, project, body, created_at FROM session_note"
                " WHERE project=$1 ORDER BY created_at DESC LIMIT $2",
                project, limit,
            )
        return [row_to_session_note(r) for r in rows]

    async def save_code_change_inner(self, change: CodeChange) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                "INSERT INTO code_change (commit_hash, file_path, diff_summary, why, changed_at)"
                " VALUES ($1, $2, $3, $4, $5)"
                " ON CONFLICT (commit_hash, file_path) DO UPDATE SET"
                " diff_summary=excluded.diff_summary, why=excluded.why,"
                " changed_at=excluded.changed_at",
                change.commit_hash, change.file_path, change.diff_summary, change.why,
                change.changed_at,
            )

    async def save_code_change(self, change: CodeChange) -> None:
        await self.save_code_change_inner(change)

    async def get_recent_changes(self, file_path: str, limit: int = 5) -> list[CodeChange]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT commit_hash, file_path, diff_summary, why, changed_at FROM code_change"
                " WHERE file_path=$1 ORDER BY changed_at DESC LIMIT $2",
                file_path, limit,
            )
        return [row_to_code_change(r) for r in rows]

    async def save_session(
        self, session_id: str, agent: str, repo: str, *, context_payload: str = ""
    ) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                "INSERT INTO sessions (id, agent, repo, started_at, ended_at, context_payload)"
                " VALUES ($1, $2, $3, $4, NULL, $5)"
                " ON CONFLICT (id) DO UPDATE SET agent=excluded.agent, repo=excluded.repo,"
                " context_payload=excluded.context_payload",
                session_id, agent, repo, datetime.now(UTC),
                json.dumps({"recall": context_payload}),
            )

    async def end_session(self, session_id: str) -> str | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            return await con.fetchval(
                "WITH upd AS ("
                " UPDATE sessions SET ended_at=$1 WHERE id=$2 AND ended_at IS NULL"
                " RETURNING repo)"
                " SELECT repo FROM upd"
                " UNION ALL"
                " SELECT repo FROM sessions WHERE id=$2 AND NOT EXISTS (SELECT 1 FROM upd)"
                " LIMIT 1",
                datetime.now(UTC), session_id,
            )

    async def all_memories(self) -> list[SessionMemory]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(f"{_MEMORY_SELECT} ORDER BY created_at, id")
        return [row_to_session_memory(r) for r in rows]

    async def all_notes(self) -> list[SessionNote]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, project, body, created_at FROM session_note ORDER BY created_at")
        return [row_to_session_note(r) for r in rows]

    async def all_code_changes(self) -> list[CodeChange]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT commit_hash, file_path, diff_summary, why, changed_at"
                " FROM code_change ORDER BY changed_at")
        return [row_to_code_change(r) for r in rows]

    async def all_sessions(self) -> list[dict]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, agent, repo, started_at, ended_at, context_payload"
                " FROM sessions ORDER BY started_at")
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
