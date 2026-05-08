from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Adr:
    id: int | None
    project: str
    title: str
    context: str
    decision: str
    rationale: str
    created_at: str


class AdrManager:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init()

    def save(self, adr: Adr) -> Adr:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO adr (project, title, context, decision, rationale, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    adr.project,
                    adr.title,
                    adr.context,
                    adr.decision,
                    adr.rationale,
                    datetime.utcnow().isoformat(),
                ),
            )
            return Adr(
                id=cur.lastrowid,
                project=adr.project,
                title=adr.title,
                context=adr.context,
                decision=adr.decision,
                rationale=adr.rationale,
                created_at=datetime.utcnow().isoformat(),
            )

    def list_for_project(self, project: str) -> list[Adr]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, project, title, context, decision, rationale, created_at "
                "FROM adr WHERE project = ? ORDER BY created_at DESC",
                (project,),
            ).fetchall()
        return [Adr(*r) for r in rows]

    def format_markdown(self, adr: Adr) -> str:
        return (
            f"# ADR: {adr.title}\n\n"
            f"**Project:** {adr.project}\n"
            f"**Date:** {adr.created_at[:10]}\n\n"
            f"## Context\n{adr.context}\n\n"
            f"## Decision\n{adr.decision}\n\n"
            f"## Rationale\n{adr.rationale}\n"
        )

    def _init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS adr (
                    id INTEGER PRIMARY KEY, project TEXT, title TEXT,
                    context TEXT, decision TEXT, rationale TEXT, created_at TEXT
                )"""
            )
