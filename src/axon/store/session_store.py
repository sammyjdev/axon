import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import BaseModel, Field

from axon.config.data_root import data_root
from axon.core.decision import Decision
from axon.core.edge import Edge
from axon.store.pending import (
    DrainResult,
    PendingPaths,
    emit_capture_warning,
    write_pending,
)

if TYPE_CHECKING:
    from axon.store.file_cache import SqliteFileCache

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _is_db_locked(exc: Exception) -> bool:
    """Return True if ``exc`` indicates SQLite write contention."""
    if not isinstance(exc, aiosqlite.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _pending_paths() -> PendingPaths:
    """Resolve the pending/quarantine layout under the AXON data root."""
    root = data_root()
    return PendingPaths(
        pending_dir=root / "pending",
        quarantine_dir=root / "pending-quarantine",
        quarantine_log=root / "quarantine.jsonl",
    )


def _warnings_log() -> Path:
    return data_root() / "capture-warnings.jsonl"


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
        self._graph_repo = None

    async def _connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._path)
            # PRAGMAs from dec-112: WAL enables concurrent readers during
            # writes; busy_timeout lets SQLite internally retry under
            # contention; synchronous=NORMAL is safe with WAL.
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA busy_timeout=5000")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
            await self._conn.commit()
        return self._conn

    async def init(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            db = await self._connection()
            await _apply_migrations(db)

    def make_file_cache(self) -> "SqliteFileCache":
        """Return a SqliteFileCache sharing this store's connection and lock.

        Call after init() so the connection exists and the file_index
        migration has been applied. The cache shares this store's asyncio.Lock
        so all SQLite writes stay serialized on a single connection.
        """
        from axon.store.file_cache import SqliteFileCache

        if self._conn is None:
            raise RuntimeError(
                "SessionStore.init() must be called before make_file_cache()"
            )
        return SqliteFileCache(self._conn, self._lock)

    # ── ADR ──────────────────────────────────────────────────────────────────

    async def _save_adr_inner(self, adr: ADR) -> int:
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

    async def save_adr(self, adr: ADR) -> int:
        try:
            return await self._save_adr_inner(adr)
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

    async def _save_code_change_inner(self, change: CodeChange) -> None:
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

    async def save_code_change(self, change: CodeChange) -> None:
        try:
            await self._save_code_change_inner(change)
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

    async def _graph(self):
        if self._graph_repo is None:
            from axon.config.runtime import load_runtime_config

            rt = load_runtime_config()
            if rt.graph_backend == "postgres":
                from axon.store.pg_graph_repository import PostgresGraphRepository

                self._graph_repo = PostgresGraphRepository(rt.pg_url)
                await self._graph_repo.ensure_schema()
            else:
                from axon.store.graph_repository import SqliteGraphRepository

                self._graph_repo = SqliteGraphRepository(self)
        return self._graph_repo

    async def add_node(
        self,
        node_id: str,
        node_type: str,
        *,
        label: str = "",
        payload: dict | None = None,
    ) -> None:
        repo = await self._graph()
        return await repo.add_node(node_id, node_type, label=label, payload=payload)

    async def add_edge(self, edge: Edge) -> None:
        repo = await self._graph()
        return await repo.add_edge(edge)

    async def get_node(self, node_id: str) -> dict[str, object] | None:
        """Return a node by id with its payload parsed, or None if absent."""
        repo = await self._graph()
        return await repo.get_node(node_id)

    async def query_subgraph(self, node_id: str, depth: int = 2) -> dict[str, object]:
        repo = await self._graph()
        return await repo.query_subgraph(node_id, depth)

    async def shortest_path(
        self, from_node: str, to_node: str, max_depth: int = 10
    ) -> list[str] | None:
        """Shortest directed path between two nodes (BFS over edges).

        Returns the node ids from ``from_node`` to ``to_node`` inclusive, or
        None if no path exists within ``max_depth`` hops.
        """
        repo = await self._graph()
        return await repo.shortest_path(from_node, to_node, max_depth)

    async def all_nodes(self) -> list[dict[str, object]]:
        """Return every persisted node (id/type/label/payload) for graph export.

        Read-only full scan used by the GLYPH bridge, which needs the whole
        graph at once rather than a seed-anchored subgraph.
        """
        repo = await self._graph()
        return await repo.all_nodes()

    async def all_edges(self) -> list[Edge]:
        """Return every persisted edge as :class:`Edge` for graph export."""
        repo = await self._graph()
        return await repo.all_edges()

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

    async def find_decision_by_git_hash(
        self, git_hash: str, *, repo: str | None = None
    ) -> Decision | None:
        async with self._lock:
            db = await self._connection()
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
        if self._graph_repo is not None and hasattr(self._graph_repo, "close"):
            await self._graph_repo.close()
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None

    async def drain_pending(self) -> DrainResult:
        """Drain ``.axon/pending/`` into the DB (dec-112).

        Each payload is dispatched to its kind-specific writer. Retryable
        DB errors leave the file in place; malformed files are quarantined.
        Returns the structured drain result.
        """
        from axon.store.pending import drain_pending as _drain

        paths = _pending_paths()

        async def sink(payload: dict) -> None:
            kind = payload.get("kind")
            if kind == "code_change":
                await self._save_code_change_inner(
                    CodeChange(
                        commit_hash=payload["commit_hash"],
                        file_path=payload["file_path"],
                        diff_summary=payload["diff_summary"],
                        why=payload.get("why", ""),
                        changed_at=datetime.fromisoformat(payload["changed_at"]),
                    )
                )
            elif kind == "adr":
                await self._save_adr_inner(
                    ADR(
                        project=payload["project"],
                        title=payload["title"],
                        context=payload["context"],
                        decision=payload["decision"],
                        rationale=payload["rationale"],
                        created_at=datetime.fromisoformat(payload["created_at"]),
                    )
                )
            else:
                raise ValueError(f"unknown payload kind: {kind!r}")

        return await _drain(paths, sink=sink, is_retryable=_is_db_locked)
