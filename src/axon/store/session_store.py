import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

DDL = """
CREATE TABLE IF NOT EXISTS adr (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    context     TEXT    NOT NULL,
    decision    TEXT    NOT NULL,
    rationale   TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS session_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT    NOT NULL,
    summary     TEXT    NOT NULL,
    raw_turns   INTEGER NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS code_change (
    commit_hash TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    diff_summary TEXT   NOT NULL,
    why         TEXT    NOT NULL DEFAULT '',
    changed_at  TEXT    NOT NULL,
    PRIMARY KEY (commit_hash, file_path)
);

CREATE TABLE IF NOT EXISTS session_note (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project    TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@dataclass
class ADR:
    project: str
    title: str
    context: str
    decision: str
    rationale: str
    id: int = 0
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = datetime.now(UTC)


@dataclass
class SessionMemory:
    project: str
    summary: str
    raw_turns: int
    id: int = 0
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = datetime.now(UTC)


@dataclass
class SessionNote:
    project: str
    body: str
    id: int = 0
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = datetime.now(UTC)


@dataclass
class CodeChange:
    commit_hash: str
    file_path: str
    diff_summary: str
    why: str = ""
    changed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.changed_at is None:
            self.changed_at = datetime.now(UTC)


class SessionStore:
    def __init__(self, db_path: str | Path = "./data/axon.db") -> None:
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

    # ── ADR ──────────────────────────────────────────────────────────────────

    async def save_adr(self, adr: ADR) -> int:
        async with self._lock:
            db = await self._connection()
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

    async def get_adrs(self, project: str, limit: int = 10) -> list[ADR]:
        async with self._lock:
            db = await self._connection()
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

    # ── Session Memory ────────────────────────────────────────────────────────

    async def save_session_memory(self, mem: SessionMemory) -> int:
        async with self._lock:
            db = await self._connection()
            cursor = await db.execute(
                "INSERT INTO session_memory (project, summary, raw_turns, created_at)"
                " VALUES (?, ?, ?, ?)",
                (mem.project, mem.summary, mem.raw_turns, mem.created_at.isoformat()),
            )
            await db.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    async def get_session_memories(self, project: str, limit: int = 3) -> list[SessionMemory]:
        async with self._lock:
            db = await self._connection()
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

    async def save_note(self, note: SessionNote) -> int:
        async with self._lock:
            db = await self._connection()
            cursor = await db.execute(
                "INSERT INTO session_note (project, body, created_at) VALUES (?, ?, ?)",
                (note.project, note.body, note.created_at.isoformat()),
            )
            await db.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    async def get_notes(self, project: str, limit: int = 10) -> list[SessionNote]:
        async with self._lock:
            db = await self._connection()
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

    async def save_code_change(self, change: CodeChange) -> None:
        async with self._lock:
            db = await self._connection()
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

    async def get_recent_changes(self, file_path: str, limit: int = 5) -> list[CodeChange]:
        async with self._lock:
            db = await self._connection()
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

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None
