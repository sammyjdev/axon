import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

DDL = """
CREATE TABLE IF NOT EXISTS outcome_record (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT    NOT NULL,
    context     TEXT    NOT NULL,
    summary     TEXT    NOT NULL,
    outcome     TEXT    NOT NULL,
    tags_json   TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);
"""


@dataclass
class OutcomeRecord:
    project: str
    context: str
    summary: str
    outcome: str
    tags: list[str]
    id: int = 0
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = datetime.now(UTC)


class OutcomeStore:
    def __init__(self, db_path: str | Path = "./data/outcomes.db") -> None:
        self._path = str(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def _connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._path)
        return self._conn

    async def init(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            db = await self._connection()
            await db.executescript(DDL)
            await db.commit()

    async def save_outcome(self, outcome: OutcomeRecord) -> int:
        async with self._lock:
            db = await self._connection()
            cursor = await db.execute(
                "INSERT INTO outcome_record"
                " (project, context, summary, outcome, tags_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    outcome.project,
                    outcome.context,
                    outcome.summary,
                    outcome.outcome,
                    json.dumps(outcome.tags),
                    outcome.created_at.isoformat(),
                ),
            )
            await db.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    async def get_outcomes_for_context(
        self,
        project: str,
        context: str,
        limit: int = 10,
    ) -> list[OutcomeRecord]:
        rows = await self._fetch_outcomes(
            "SELECT * FROM outcome_record WHERE project = ? AND context = ?"
            " ORDER BY created_at DESC LIMIT ?",
            (project, context, limit),
        )
        return [self._row_to_outcome(row) for row in rows]

    async def find_outcomes_by_tag(
        self,
        tag: str,
        *,
        project: str | None = None,
        context: str | None = None,
        limit: int = 10,
    ) -> list[OutcomeRecord]:
        conditions: list[str] = []
        params: list[str | int] = []

        if project is not None:
            conditions.append("project = ?")
            params.append(project)
        if context is not None:
            conditions.append("context = ?")
            params.append(context)

        conditions.append("tags_json LIKE ?")
        params.append(self._tag_pattern(tag))
        params.append(limit)

        where_clause = " AND ".join(conditions)
        rows = await self._fetch_outcomes(
            f"SELECT * FROM outcome_record WHERE {where_clause}"
            " ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return [self._row_to_outcome(row) for row in rows]

    async def _fetch_outcomes(
        self,
        query: str,
        params: tuple[str | int, ...],
    ) -> list[aiosqlite.Row]:
        async with self._lock:
            db = await self._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(query, params)
        return rows

    def _row_to_outcome(self, row: aiosqlite.Row) -> OutcomeRecord:
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
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None
