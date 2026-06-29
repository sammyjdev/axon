"""Postgres-backed OutcomeStore (dec-121 Phase 3 — retires data/outcomes.db).

Records expansion outcomes for context-aware recall. Same method surface as the
former aiosqlite store; only the backend changed. Like FailureStore it opens a
fresh connection per call because the expansion service drives it through
independent ``asyncio.run`` calls (an asyncpg pool is loop-bound).
"""
import json
from datetime import UTC, datetime

import asyncpg
from pydantic import BaseModel, Field

_DDL = """
CREATE TABLE IF NOT EXISTS outcome_record (
    id          bigserial PRIMARY KEY,
    project     text NOT NULL,
    context     text NOT NULL,
    summary     text NOT NULL,
    outcome     text NOT NULL,
    tags_json   text NOT NULL,
    created_at  text NOT NULL
)
"""


class OutcomeRecord(BaseModel):
    project: str
    context: str
    summary: str
    outcome: str
    tags: list[str]
    id: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OutcomeStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def init(self) -> None:
        con = await asyncpg.connect(self._dsn)
        try:
            await con.execute(_DDL)
        finally:
            await con.close()

    async def save_outcome(self, outcome: OutcomeRecord) -> int:
        con = await asyncpg.connect(self._dsn)
        try:
            return await con.fetchval(
                "INSERT INTO outcome_record"
                " (project, context, summary, outcome, tags_json, created_at)"
                " VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                outcome.project, outcome.context, outcome.summary, outcome.outcome,
                json.dumps(outcome.tags), outcome.created_at.isoformat(),
            )
        finally:
            await con.close()

    async def get_outcomes_for_context(
        self, project: str, context: str, limit: int = 10
    ) -> list[OutcomeRecord]:
        rows = await self._fetch(
            "SELECT * FROM outcome_record WHERE project = $1 AND context = $2"
            " ORDER BY created_at DESC LIMIT $3",
            project, context, limit,
        )
        return [self._row_to_outcome(row) for row in rows]

    async def find_outcomes_by_tag(
        self, tag: str, *, project: str | None = None,
        context: str | None = None, limit: int = 10,
    ) -> list[OutcomeRecord]:
        conditions: list[str] = []
        params: list[object] = []
        if project is not None:
            params.append(project)
            conditions.append(f"project = ${len(params)}")
        if context is not None:
            params.append(context)
            conditions.append(f"context = ${len(params)}")
        params.append(self._tag_pattern(tag))
        conditions.append(f"tags_json LIKE ${len(params)}")
        params.append(limit)
        where_clause = " AND ".join(conditions)
        rows = await self._fetch(
            f"SELECT * FROM outcome_record WHERE {where_clause}"
            f" ORDER BY created_at DESC LIMIT ${len(params)}",
            *params,
        )
        return [self._row_to_outcome(row) for row in rows]

    async def _fetch(self, query: str, *params: object) -> list[asyncpg.Record]:
        con = await asyncpg.connect(self._dsn)
        try:
            return await con.fetch(query, *params)
        finally:
            await con.close()

    def _row_to_outcome(self, row: asyncpg.Record) -> OutcomeRecord:
        return OutcomeRecord(
            id=row["id"],
            project=row["project"],
            context=row["context"],
            summary=row["summary"],
            outcome=row["outcome"],
            tags=json.loads(row["tags_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _tag_pattern(self, tag: str) -> str:
        return f'%"{tag}"%'

    async def close(self) -> None:
        """No-op: connections are opened and closed per call."""
