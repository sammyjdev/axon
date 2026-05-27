import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from pydantic import BaseModel, Field

from axon.core.decision import Decision
from axon.core.edge import Edge

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def _apply_migrations(db: aiosqlite.Connection) -> None:
    """Apply pending SQL migrations in filename order, tracked in schema_version."""
    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    await db.commit()
    cursor = await db.execute("SELECT version FROM schema_version")
    applied = {row[0] for row in await cursor.fetchall()}
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if path.stem in applied:
            continue
        await db.executescript(path.read_text(encoding="utf-8"))
        await db.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (path.stem, datetime.now(UTC).isoformat()),
        )
        await db.commit()


class ADR(BaseModel):
    project: str
    title: str
    context: str
    decision: str
    rationale: str
    id: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionMemory(BaseModel):
    project: str
    summary: str
    raw_turns: int
    id: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionNote(BaseModel):
    project: str
    body: str
    id: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CodeChange(BaseModel):
    commit_hash: str
    file_path: str
    diff_summary: str
    why: str = ""
    changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
            await _apply_migrations(db)

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

    # ── Graph / Decisions ─────────────────────────────────────────────────────

    async def add_node(
        self,
        node_id: str,
        node_type: str,
        *,
        label: str = "",
        payload: dict | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        async with self._lock:
            db = await self._connection()
            await db.execute(
                "INSERT INTO nodes (id, type, label, payload, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " type=excluded.type, label=excluded.label,"
                " payload=excluded.payload, updated_at=excluded.updated_at",
                (node_id, node_type, label, json.dumps(payload or {}), now, now),
            )
            await db.commit()

    async def add_edge(self, edge: Edge) -> None:
        async with self._lock:
            db = await self._connection()
            await db.execute(
                "INSERT INTO edges (source_id, target_id, type, payload, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    edge.source_id,
                    edge.target_id,
                    edge.type,
                    json.dumps(edge.payload) if edge.payload is not None else None,
                    datetime.now(UTC).isoformat(),
                ),
            )
            await db.commit()

    async def get_node(self, node_id: str) -> dict[str, object] | None:
        """Return a node by id with its payload parsed, or None if absent."""
        async with self._lock:
            db = await self._connection()
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, type, label, payload, created_at, updated_at"
                " FROM nodes WHERE id = ?",
                (node_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "type": row["type"],
            "label": row["label"],
            "payload": json.loads(row["payload"]) if row["payload"] else {},
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def query_subgraph(self, node_id: str, depth: int = 2) -> dict[str, object]:
        visited: set[str] = {node_id}
        frontier: set[str] = {node_id}
        edges: list[dict[str, str]] = []
        async with self._lock:
            db = await self._connection()
            db.row_factory = aiosqlite.Row
            for _ in range(depth):
                if not frontier:
                    break
                placeholders = ",".join("?" * len(frontier))
                rows = await db.execute_fetchall(
                    "SELECT source_id, target_id, type FROM edges"
                    f" WHERE source_id IN ({placeholders})",
                    tuple(frontier),
                )
                next_frontier: set[str] = set()
                for row in rows:
                    edges.append(
                        {
                            "source": row["source_id"],
                            "target": row["target_id"],
                            "type": row["type"],
                        }
                    )
                    if row["target_id"] not in visited:
                        visited.add(row["target_id"])
                        next_frontier.add(row["target_id"])
                frontier = next_frontier
        return {"root": node_id, "nodes": sorted(visited), "edges": edges}

    async def shortest_path(
        self, from_node: str, to_node: str, max_depth: int = 10
    ) -> list[str] | None:
        """Shortest directed path between two nodes (BFS over edges).

        Returns the node ids from ``from_node`` to ``to_node`` inclusive, or
        None if no path exists within ``max_depth`` hops.
        """
        if from_node == to_node:
            return [from_node]
        visited: set[str] = {from_node}
        parent: dict[str, str] = {}
        frontier: list[str] = [from_node]
        async with self._lock:
            db = await self._connection()
            db.row_factory = aiosqlite.Row
            for _ in range(max_depth):
                if not frontier:
                    break
                placeholders = ",".join("?" * len(frontier))
                rows = await db.execute_fetchall(
                    "SELECT source_id, target_id FROM edges"
                    f" WHERE source_id IN ({placeholders})",
                    tuple(frontier),
                )
                next_frontier: list[str] = []
                for row in rows:
                    target = row["target_id"]
                    if target in visited:
                        continue
                    visited.add(target)
                    parent[target] = row["source_id"]
                    if target == to_node:
                        path = [to_node]
                        while path[-1] != from_node:
                            path.append(parent[path[-1]])
                        return list(reversed(path))
                    next_frontier.append(target)
                frontier = next_frontier
        return None

    async def save_decision(self, decision: Decision) -> None:
        async with self._lock:
            db = await self._connection()
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
        async with self._lock:
            db = await self._connection()
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

    async def find_decision_by_git_hash(self, git_hash: str) -> Decision | None:
        async with self._lock:
            db = await self._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT frontmatter FROM decisions"
                " WHERE json_extract(frontmatter, '$.git_hash') = ?"
                " LIMIT 1",
                (git_hash,),
            )
        if not rows:
            return None
        return Decision(**json.loads(rows[0]["frontmatter"]))

    async def find_decisions_by_repo(self, repo: str, limit: int = 20) -> list[Decision]:
        async with self._lock:
            db = await self._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT frontmatter FROM decisions"
                " WHERE json_extract(frontmatter, '$.repo') = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (repo, limit),
            )
        return [Decision(**json.loads(row["frontmatter"])) for row in rows]

    async def save_session(
        self, session_id: str, agent: str, repo: str, *, context_payload: str = ""
    ) -> None:
        async with self._lock:
            db = await self._connection()
            await db.execute(
                "INSERT OR REPLACE INTO sessions"
                " (id, agent, repo, started_at, ended_at, context_payload)"
                " VALUES (?, ?, ?, ?, NULL, ?)",
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
        """Mark a session ended; return its repo, or None if the id is unknown."""
        async with self._lock:
            db = await self._connection()
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT repo FROM sessions WHERE id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            if row is not None:
                await db.execute(
                    "UPDATE sessions SET ended_at = ? WHERE id = ?",
                    (datetime.now(UTC).isoformat(), session_id),
                )
                await db.commit()
        return row["repo"] if row is not None else None

    async def next_decision_id(self) -> str:
        """Return the next sequential decision id (dec-NNN, zero-padded)."""
        async with self._lock:
            db = await self._connection()
            cursor = await db.execute("SELECT COUNT(*) FROM decisions")
            row = await cursor.fetchone()
        count = row[0] if row else 0
        return f"dec-{count + 1:03d}"

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None
