import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class SessionEntry:
    id: int
    project: str
    summary: str
    raw_turns: int
    created_at: str


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init()

    def save(self, project: str, summary: str, raw_turns: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO session_memory (project, summary, raw_turns, created_at) VALUES (?,?,?,?)",
                (project, summary, raw_turns, datetime.utcnow().isoformat()),
            )

    def load_recent(self, project: str, limit: int = 3) -> list[SessionEntry]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, project, summary, raw_turns, created_at "
                "FROM session_memory WHERE project = ? ORDER BY created_at DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        return [SessionEntry(*r) for r in rows]

    def last_context(self) -> str | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT project FROM session_memory ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def _init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS session_memory (
                    id INTEGER PRIMARY KEY, project TEXT, summary TEXT,
                    raw_turns INTEGER, created_at TEXT
                )"""
            )


def format_entry(entry: SessionEntry) -> str:
    return f"[{entry.created_at} — {entry.raw_turns} turns]\n{entry.summary}"
