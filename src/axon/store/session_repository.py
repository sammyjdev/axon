"""SessionRepository Protocol and SqliteSessionRepository (dec-121 wave 4, Task 2).

Pure refactor: SQL moved verbatim from SessionStore; self -> self._session.
_save_code_change_inner renamed save_code_change_inner.
save_code_change keeps the db-locked pending fallback.
all_memories/all_notes/all_code_changes/all_sessions are full-scan helpers
for the data-copy script (Task 5).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import aiosqlite

from axon.store.pending import (
    emit_capture_warning,
    write_pending,
)


def _is_db_locked(exc: Exception) -> bool:
    if not isinstance(exc, aiosqlite.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _pending_paths():
    from axon.config.data_root import data_root
    from axon.store.pending import PendingPaths

    root = data_root()
    return PendingPaths(
        pending_dir=root / "pending",
        quarantine_dir=root / "pending-quarantine",
        quarantine_log=root / "quarantine.jsonl",
    )


def _warnings_log():
    from axon.config.data_root import data_root

    return data_root() / "capture-warnings.jsonl"


@runtime_checkable
class SessionRepository(Protocol):
    async def save_session_memory(self, mem) -> int: ...
    async def get_session_memories(self, project: str, limit: int = 3) -> list: ...
    async def save_note(self, note) -> int: ...
    async def get_notes(self, project: str, limit: int = 10) -> list: ...
    async def save_code_change_inner(self, change) -> None: ...
    async def save_code_change(self, change) -> None: ...
    async def get_recent_changes(self, file_path: str, limit: int = 5) -> list: ...
    async def save_session(
        self, session_id: str, agent: str, repo: str, *, context_payload: str = ""
    ) -> None: ...
    async def end_session(self, session_id: str) -> str | None: ...
    async def all_memories(self) -> list: ...
    async def all_notes(self) -> list: ...
    async def all_code_changes(self) -> list: ...
    async def all_sessions(self) -> list: ...


class SqliteSessionRepository:
    """SQLite-backed SessionRepository (wraps SessionStore for connection/lock)."""

    def __init__(self, session) -> None:
        # session is the SessionStore instance (provides _lock, _connection)
        self._session = session

    # ── Session Memory ────────────────────────────────────────────────────────

    async def save_session_memory(self, mem) -> int:
        from axon.store.session_store import SessionMemory  # noqa: F401 (type guard only)

        async with self._session._lock:
            db = await self._session._connection()
            cursor = await db.execute(
                "INSERT INTO session_memory (project, summary, raw_turns, created_at)"
                " VALUES (?, ?, ?, ?)",
                (mem.project, mem.summary, mem.raw_turns, mem.created_at.isoformat()),
            )
            await db.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    async def get_session_memories(self, project: str, limit: int = 3) -> list:
        from axon.store.session_store import SessionMemory

        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM session_memory WHERE project = ? ORDER BY created_at DESC LIMIT ?",
                (project, limit),
            )
        return [
            SessionMemory(
                id=r["id"],
                project=r["project"],
                summary=r["summary"],
                raw_turns=r["raw_turns"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    # ── Session Note ──────────────────────────────────────────────────────────

    async def save_note(self, note) -> int:
        async with self._session._lock:
            db = await self._session._connection()
            cursor = await db.execute(
                "INSERT INTO session_note (project, body, created_at) VALUES (?, ?, ?)",
                (note.project, note.body, note.created_at.isoformat()),
            )
            await db.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    async def get_notes(self, project: str, limit: int = 10) -> list:
        from axon.store.session_store import SessionNote

        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM session_note WHERE project = ? ORDER BY created_at DESC LIMIT ?",
                (project, limit),
            )
        return [
            SessionNote(
                id=r["id"],
                project=r["project"],
                body=r["body"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    # ── Code Change ───────────────────────────────────────────────────────────

    async def save_code_change_inner(self, change) -> None:
        async with self._session._lock:
            db = await self._session._connection()
            await db.execute(
                "INSERT OR REPLACE INTO code_change"
                " (commit_hash, file_path, diff_summary, why, changed_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    change.commit_hash,
                    change.file_path,
                    change.diff_summary,
                    change.why,
                    change.changed_at.isoformat(),
                ),
            )
            await db.commit()

    async def save_code_change(self, change) -> None:
        try:
            await self.save_code_change_inner(change)
        except aiosqlite.OperationalError as exc:
            if not _is_db_locked(exc):
                raise
            paths = _pending_paths()
            await write_pending(
                payload={
                    "kind": "code_change",
                    "commit_hash": change.commit_hash,
                    "file_path": change.file_path,
                    "diff_summary": change.diff_summary,
                    "why": change.why,
                    "changed_at": change.changed_at.isoformat(),
                },
                commit_hash=change.commit_hash,
                paths=paths,
            )
            emit_capture_warning(
                _warnings_log(),
                kind="code_change",
                commit_hash=change.commit_hash,
                reason=str(exc),
            )

    async def get_recent_changes(self, file_path: str, limit: int = 5) -> list:
        from axon.store.session_store import CodeChange

        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM code_change WHERE file_path = ? ORDER BY changed_at DESC LIMIT ?",
                (file_path, limit),
            )
        return [
            CodeChange(
                commit_hash=r["commit_hash"],
                file_path=r["file_path"],
                diff_summary=r["diff_summary"],
                why=r["why"],
                changed_at=datetime.fromisoformat(r["changed_at"]),
            )
            for r in rows
        ]

    # ── Sessions ──────────────────────────────────────────────────────────────

    async def save_session(
        self, session_id: str, agent: str, repo: str, *, context_payload: str = ""
    ) -> None:
        async with self._session._lock:
            db = await self._session._connection()
            await db.execute(
                "INSERT INTO sessions (id, agent, repo, started_at, ended_at, context_payload)"
                " VALUES (?, ?, ?, ?, NULL, ?)"
                " ON CONFLICT (id) DO UPDATE SET agent=excluded.agent, repo=excluded.repo,"
                " context_payload=excluded.context_payload",
                (
                    session_id,
                    agent,
                    repo,
                    datetime.now(UTC).isoformat(),
                    json.dumps({"recall": context_payload}),
                ),
            )
            await db.commit()

    async def end_session(self, session_id: str) -> str | None:
        """Mark a session ended; return its repo, or None if the id is unknown.

        First-close-wins: ended_at is only written once; subsequent calls return
        repo without re-stamping.
        """
        async with self._session._lock:
            db = await self._session._connection()
            await db.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (datetime.now(UTC).isoformat(), session_id),
            )
            await db.commit()
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT repo FROM sessions WHERE id = ?", (session_id,))
            row = await cursor.fetchone()
        return row["repo"] if row is not None else None

    # ── Full-scan helpers (for data-copy script) ──────────────────────────────

    async def all_memories(self):
        import aiosqlite as _a

        from axon.store.session_store import SessionMemory

        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = _a.Row
            rows = await db.execute_fetchall("SELECT * FROM session_memory ORDER BY created_at")
        return [
            SessionMemory(
                id=r["id"],
                project=r["project"],
                summary=r["summary"],
                raw_turns=r["raw_turns"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    async def all_notes(self):
        import aiosqlite as _a

        from axon.store.session_store import SessionNote

        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = _a.Row
            rows = await db.execute_fetchall("SELECT * FROM session_note ORDER BY created_at")
        return [
            SessionNote(
                id=r["id"],
                project=r["project"],
                body=r["body"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    async def all_code_changes(self):
        import aiosqlite as _a

        from axon.store.session_store import CodeChange

        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = _a.Row
            rows = await db.execute_fetchall("SELECT * FROM code_change ORDER BY changed_at")
        return [
            CodeChange(
                commit_hash=r["commit_hash"],
                file_path=r["file_path"],
                diff_summary=r["diff_summary"],
                why=r["why"],
                changed_at=datetime.fromisoformat(r["changed_at"]),
            )
            for r in rows
        ]

    async def all_sessions(self):
        import aiosqlite as _a

        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = _a.Row
            rows = await db.execute_fetchall(
                "SELECT id, agent, repo, started_at, ended_at, context_payload"
                " FROM sessions ORDER BY started_at"
            )
        return [dict(r) for r in rows]
