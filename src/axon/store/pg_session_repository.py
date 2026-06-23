"""Postgres-backed SessionRepository (dec-121 step 3, wave 4). Plain columns;
memory/note inserts use RETURNING id; code_change/session use ON CONFLICT;
no SQLite-lock fallback."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import asyncpg

from axon.store._session_columns import (
    row_to_code_change,
    row_to_session_memory,
    row_to_session_note,
)
from axon.store.session_store import CodeChange, SessionMemory, SessionNote


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
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                "CREATE TABLE IF NOT EXISTS session_memory (id bigserial PRIMARY KEY,"
                " project text NOT NULL, summary text NOT NULL, raw_turns integer NOT NULL,"
                " created_at text NOT NULL)"
            )
            await con.execute(
                "CREATE TABLE IF NOT EXISTS session_note (id bigserial PRIMARY KEY,"
                " project text NOT NULL, body text NOT NULL, created_at text NOT NULL)"
            )
            await con.execute(
                "CREATE TABLE IF NOT EXISTS code_change (commit_hash text NOT NULL,"
                " file_path text NOT NULL, diff_summary text NOT NULL,"
                " why text NOT NULL DEFAULT '', changed_at text NOT NULL,"
                " PRIMARY KEY (commit_hash, file_path))"
            )
            await con.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id text PRIMARY KEY, agent text NOT NULL,"
                " repo text NOT NULL, started_at text NOT NULL, ended_at text,"
                " context_payload text NOT NULL DEFAULT '{}')"
            )

    async def save_session_memory(self, mem: SessionMemory) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            return await con.fetchval(
                "INSERT INTO session_memory (project, summary, raw_turns, created_at)"
                " VALUES ($1, $2, $3, $4) RETURNING id",
                mem.project, mem.summary, mem.raw_turns, mem.created_at.isoformat(),
            )

    async def get_session_memories(self, project: str, limit: int = 3) -> list[SessionMemory]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, project, summary, raw_turns, created_at FROM session_memory"
                " WHERE project=$1 ORDER BY created_at DESC LIMIT $2",
                project, limit,
            )
        return [row_to_session_memory(r) for r in rows]

    async def save_note(self, note: SessionNote) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            return await con.fetchval(
                "INSERT INTO session_note (project, body, created_at)"
                " VALUES ($1, $2, $3) RETURNING id",
                note.project, note.body, note.created_at.isoformat(),
            )

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
                change.changed_at.isoformat(),
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
                " started_at=excluded.started_at, ended_at=excluded.ended_at,"
                " context_payload=excluded.context_payload",
                session_id, agent, repo, datetime.now(UTC).isoformat(),
                json.dumps({"recall": context_payload}),
            )

    async def end_session(self, session_id: str) -> str | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            repo = await con.fetchval("SELECT repo FROM sessions WHERE id=$1", session_id)
            if repo is not None:
                await con.execute(
                    "UPDATE sessions SET ended_at=$1 WHERE id=$2",
                    datetime.now(UTC).isoformat(), session_id,
                )
        return repo

    async def all_memories(self) -> list[SessionMemory]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, project, summary, raw_turns, created_at FROM session_memory"
                " ORDER BY created_at")
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
