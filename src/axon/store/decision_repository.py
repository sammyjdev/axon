"""DecisionRepository Protocol + SqliteDecisionRepository.

The SqliteDecisionRepository holds the original SessionStore decision/ADR SQL,
sharing the session's connection and lock. This is a pure extract - no SQL or
return shapes are changed.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol, runtime_checkable

import aiosqlite

from axon.core.decision import Decision
from axon.store.session_store import (
    ADR,
    _is_db_locked,
    _pending_paths,
    _warnings_log,
)
from axon.store.pending import (
    emit_capture_warning,
    write_pending,
)


@runtime_checkable
class DecisionRepository(Protocol):
    async def save_decision(self, decision: Decision) -> None: ...

    async def find_decisions_by_symbol(self, symbol_id: str) -> list[Decision]: ...

    async def find_decision_by_git_hash(
        self, git_hash: str, *, repo: str | None = None
    ) -> Decision | None: ...

    async def find_decisions_by_repo(self, repo: str, limit: int = 20) -> list[Decision]: ...

    async def next_decision_id(self) -> str: ...

    async def save_adr_inner(self, adr: ADR) -> int: ...

    async def save_adr(self, adr: ADR) -> int: ...

    async def get_adrs(self, project: str, limit: int = 10) -> list[ADR]: ...

    async def all_decisions(self) -> list[Decision]: ...


class SqliteDecisionRepository:
    """The original SessionStore decision/ADR SQL, sharing the session's conn+lock."""

    def __init__(self, session) -> None:
        self._session = session

    # ── ADR ──────────────────────────────────────────────────────────────────

    async def save_adr_inner(self, adr: ADR) -> int:
        async with self._session._lock:
            db = await self._session._connection()
            cursor = await db.execute(
                "INSERT INTO adr (project, title, context, decision, rationale, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    adr.project,
                    adr.title,
                    adr.context,
                    adr.decision,
                    adr.rationale,
                    adr.created_at.isoformat(),
                ),
            )
            await db.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    async def save_adr(self, adr: ADR) -> int:
        try:
            return await self.save_adr_inner(adr)
        except aiosqlite.OperationalError as exc:
            if not _is_db_locked(exc):
                raise
            # Fallback (dec-112): persist to pending, warn, return 0
            paths = _pending_paths()
            await write_pending(
                payload={
                    "kind": "adr",
                    "project": adr.project,
                    "title": adr.title,
                    "context": adr.context,
                    "decision": adr.decision,
                    "rationale": adr.rationale,
                    "created_at": adr.created_at.isoformat(),
                },
                commit_hash=adr.project,
                paths=paths,
            )
            emit_capture_warning(
                _warnings_log(),
                kind="adr",
                commit_hash=adr.project,
                reason=str(exc),
            )
            return 0

    async def get_adrs(self, project: str, limit: int = 10) -> list[ADR]:
        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM adr WHERE project = ? ORDER BY created_at DESC LIMIT ?",
                (project, limit),
            )
        return [
            ADR(
                id=r["id"],
                project=r["project"],
                title=r["title"],
                context=r["context"],
                decision=r["decision"],
                rationale=r["rationale"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    # ── Decisions ─────────────────────────────────────────────────────────────

    async def save_decision(self, decision: Decision) -> None:
        async with self._session._lock:
            db = await self._session._connection()
            await db.execute(
                "INSERT OR REPLACE INTO decisions"
                " (id, frontmatter, body, vault_path, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    decision.id,
                    json.dumps(decision.model_dump(mode="json")),
                    decision.summary,
                    None,
                    decision.timestamp.isoformat(),
                ),
            )
            await db.commit()

    async def find_decisions_by_symbol(self, symbol_id: str) -> list[Decision]:
        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT frontmatter FROM decisions"
                " WHERE EXISTS ("
                "   SELECT 1 FROM json_each(decisions.frontmatter, '$.symbols')"
                "   WHERE value = ?)"
                " ORDER BY created_at DESC",
                (symbol_id,),
            )
        return [Decision(**json.loads(row["frontmatter"])) for row in rows]

    async def find_decision_by_git_hash(
        self, git_hash: str, *, repo: str | None = None
    ) -> Decision | None:
        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = aiosqlite.Row
            where = "WHERE json_extract(frontmatter, '$.git_hash') = ?"
            params: list[object] = [git_hash]
            if repo is not None:
                where += " AND json_extract(frontmatter, '$.repo') = ?"
                params.append(repo)
            rows = await db.execute_fetchall(
                f"SELECT frontmatter FROM decisions {where}"
                " ORDER BY created_at DESC LIMIT 1",
                tuple(params),
            )
        if not rows:
            return None
        return Decision(**json.loads(rows[0]["frontmatter"]))

    async def find_decisions_by_repo(self, repo: str, limit: int = 20) -> list[Decision]:
        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT frontmatter FROM decisions"
                " WHERE json_extract(frontmatter, '$.repo') = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (repo, limit),
            )
        return [Decision(**json.loads(row["frontmatter"])) for row in rows]

    async def next_decision_id(self) -> str:
        """Return the next sequential decision id (dec-NNN, zero-padded)."""
        async with self._session._lock:
            db = await self._session._connection()
            cursor = await db.execute("SELECT COUNT(*) FROM decisions")
            row = await cursor.fetchone()
        count = row[0] if row else 0
        return f"dec-{count + 1:03d}"

    async def all_decisions(self) -> list[Decision]:
        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT frontmatter FROM decisions ORDER BY created_at"
            )
        return [Decision(**json.loads(r["frontmatter"])) for r in rows]
