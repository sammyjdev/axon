"""Postgres-backed FailureStore (dec-121 Phase 3 — retires data/failures.db).

Records operation failures for the expansion subsystem's diagnostics. Same
method surface as the former aiosqlite store; only the backend changed.

Uses a fresh connection per call (no persistent pool): the expansion service
drives these stores through independent ``asyncio.run`` calls, and an asyncpg
pool is bound to the loop that created it, so a pool could not be reused across
those calls. The store is low-traffic, so per-call connect is the simple fit.
"""
import json
from datetime import UTC, datetime

import asyncpg
from pydantic import BaseModel, Field

_DDL = """
CREATE TABLE IF NOT EXISTS failure_record (
    id              bigserial PRIMARY KEY,
    project         text NOT NULL,
    operation       text NOT NULL,
    error_message   text NOT NULL,
    probable_cause  text NOT NULL,
    tags_json       text NOT NULL,
    created_at      text NOT NULL
)
"""


class FailureRecord(BaseModel):
    project: str
    operation: str
    error_message: str
    probable_cause: str
    tags: list[str]
    id: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FailureStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def init(self) -> None:
        con = await asyncpg.connect(self._dsn)
        try:
            await con.execute(_DDL)
        finally:
            await con.close()

    async def save_failure(self, failure: FailureRecord) -> int:
        con = await asyncpg.connect(self._dsn)
        try:
            return await con.fetchval(
                "INSERT INTO failure_record"
                " (project, operation, error_message, probable_cause, tags_json, created_at)"
                " VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                failure.project, failure.operation, failure.error_message,
                failure.probable_cause, json.dumps(failure.tags), failure.created_at.isoformat(),
            )
        finally:
            await con.close()

    async def get_recent_failures(self, project: str, limit: int = 10) -> list[FailureRecord]:
        rows = await self._fetch(
            "SELECT * FROM failure_record WHERE project = $1"
            " ORDER BY created_at DESC LIMIT $2",
            project, limit,
        )
        return [self._row_to_failure(row) for row in rows]

    async def find_failures_by_tag(
        self, tag: str, *, project: str | None = None, limit: int = 10
    ) -> list[FailureRecord]:
        if project is None:
            rows = await self._fetch(
                "SELECT * FROM failure_record WHERE tags_json LIKE $1"
                " ORDER BY created_at DESC LIMIT $2",
                self._tag_pattern(tag), limit,
            )
        else:
            rows = await self._fetch(
                "SELECT * FROM failure_record WHERE project = $1 AND tags_json LIKE $2"
                " ORDER BY created_at DESC LIMIT $3",
                project, self._tag_pattern(tag), limit,
            )
        return [self._row_to_failure(row) for row in rows]

    async def get_repeated_failures(
        self, project: str, *, min_occurrences: int = 2, limit: int = 10
    ) -> list[tuple[str, int]]:
        rows = await self._fetch(
            "SELECT probable_cause, COUNT(*) AS occurrences"
            " FROM failure_record WHERE project = $1"
            " GROUP BY probable_cause"
            " HAVING COUNT(*) >= $2"
            " ORDER BY occurrences DESC, probable_cause ASC LIMIT $3",
            project, min_occurrences, limit,
        )
        return [(str(row["probable_cause"]), int(row["occurrences"])) for row in rows]

    async def _fetch(self, query: str, *params: object) -> list[asyncpg.Record]:
        con = await asyncpg.connect(self._dsn)
        try:
            return await con.fetch(query, *params)
        finally:
            await con.close()

    def _row_to_failure(self, row: asyncpg.Record) -> FailureRecord:
        return FailureRecord(
            id=row["id"],
            project=row["project"],
            operation=row["operation"],
            error_message=row["error_message"],
            probable_cause=row["probable_cause"],
            tags=json.loads(row["tags_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _tag_pattern(self, tag: str) -> str:
        return f'%"{tag}"%'

    async def close(self) -> None:
        """No-op: connections are opened and closed per call."""
