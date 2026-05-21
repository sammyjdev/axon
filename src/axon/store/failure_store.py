import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from pydantic import BaseModel, Field

DDL = """
CREATE TABLE IF NOT EXISTS failure_record (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project         TEXT    NOT NULL,
    operation       TEXT    NOT NULL,
    error_message   TEXT    NOT NULL,
    probable_cause  TEXT    NOT NULL,
    tags_json       TEXT    NOT NULL,
    created_at      TEXT    NOT NULL
);
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
    def __init__(self, db_path: str | Path = "./data/failures.db") -> None:
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

    async def save_failure(self, failure: FailureRecord) -> int:
        async with self._lock:
            db = await self._connection()
            cursor = await db.execute(
                "INSERT INTO failure_record"
                " (project, operation, error_message, probable_cause, tags_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    failure.project,
                    failure.operation,
                    failure.error_message,
                    failure.probable_cause,
                    json.dumps(failure.tags),
                    failure.created_at.isoformat(),
                ),
            )
            await db.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    async def get_recent_failures(self, project: str, limit: int = 10) -> list[FailureRecord]:
        rows = await self._fetch_failures(
            "SELECT * FROM failure_record WHERE project = ?"
            " ORDER BY created_at DESC LIMIT ?",
            (project, limit),
        )
        return [self._row_to_failure(row) for row in rows]

    async def find_failures_by_tag(
        self,
        tag: str,
        *,
        project: str | None = None,
        limit: int = 10,
    ) -> list[FailureRecord]:
        if project is None:
            query = (
                "SELECT * FROM failure_record WHERE tags_json LIKE ?"
                " ORDER BY created_at DESC LIMIT ?"
            )
            params = (self._tag_pattern(tag), limit)
        else:
            query = (
                "SELECT * FROM failure_record WHERE project = ? AND tags_json LIKE ?"
                " ORDER BY created_at DESC LIMIT ?"
            )
            params = (project, self._tag_pattern(tag), limit)

        rows = await self._fetch_failures(query, params)
        return [self._row_to_failure(row) for row in rows]

    async def get_repeated_failures(
        self,
        project: str,
        *,
        min_occurrences: int = 2,
        limit: int = 10,
    ) -> list[tuple[str, int]]:
        async with self._lock:
            db = await self._connection()
            rows = await db.execute_fetchall(
                "SELECT probable_cause, COUNT(*) AS occurrences"
                " FROM failure_record WHERE project = ?"
                " GROUP BY probable_cause"
                " HAVING COUNT(*) >= ?"
                " ORDER BY occurrences DESC, probable_cause ASC LIMIT ?",
                (project, min_occurrences, limit),
            )
        return [(str(row[0]), int(row[1])) for row in rows]

    async def _fetch_failures(
        self,
        query: str,
        params: tuple[str | int, ...],
    ) -> list[aiosqlite.Row]:
        async with self._lock:
            db = await self._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(query, params)
        return rows

    def _row_to_failure(self, row: aiosqlite.Row) -> FailureRecord:
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
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None
