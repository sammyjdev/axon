import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from axon.core.decision import Decision
from axon.core.edge import Edge
from axon.store.pending import DrainResult, _pending_paths


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
        # db_path is retained for call-site compatibility; the relational
        # backend is Postgres-only (dec-121 Phase 3) and ignores it.
        self._path = str(db_path)
        self._repo_init_lock = asyncio.Lock()
        self._graph_repo = None
        self._decision_repo = None
        self._session_repo = None

    async def init(self) -> None:
        """No-op: the Postgres repositories ensure their own schema lazily on
        first access (see _graph/_decisions/_sessions)."""

    # ── ADR ──────────────────────────────────────────────────────────────────

    async def _save_adr_inner(self, adr: ADR) -> int:
        return await (await self._decisions()).save_adr_inner(adr)

    async def save_adr(self, adr: ADR) -> int:
        return await self._save_adr_inner(adr)

    async def get_adrs(self, project: str, limit: int = 10) -> list[ADR]:
        repo = await self._decisions()
        return await repo.get_adrs(project, limit)

    # ── Session Memory ────────────────────────────────────────────────────────

    async def save_session_memory(self, mem: SessionMemory) -> int:
        repo = await self._sessions()
        return await repo.save_session_memory(mem)

    async def get_session_memories(self, project: str, limit: int = 3) -> list[SessionMemory]:
        repo = await self._sessions()
        return await repo.get_session_memories(project, limit)

    # ── Session Note ──────────────────────────────────────────────────────────

    async def save_note(self, note: SessionNote) -> int:
        repo = await self._sessions()
        return await repo.save_note(note)

    async def get_notes(self, project: str, limit: int = 10) -> list[SessionNote]:
        repo = await self._sessions()
        return await repo.get_notes(project, limit)

    # ── Code Change ───────────────────────────────────────────────────────────

    async def _save_code_change_inner(self, change: CodeChange) -> None:
        """Thin delegator kept for monkeypatch-based tests."""
        return await (await self._sessions()).save_code_change_inner(change)

    async def save_code_change(self, change: CodeChange) -> None:
        # Thin delegation: transient PG errors propagate to the caller (e.g. the
        # commit hook), which owns retry/logging. They are not swallowed here.
        repo = await self._sessions()
        await repo.save_code_change(change)

    async def get_recent_changes(self, file_path: str, limit: int = 5) -> list[CodeChange]:
        repo = await self._sessions()
        return await repo.get_recent_changes(file_path, limit)

    # ── Graph / Decisions ─────────────────────────────────────────────────────

    async def _graph(self):
        if self._graph_repo is None:
            async with self._repo_init_lock:
                if self._graph_repo is None:
                    from axon.config.runtime import load_runtime_config
                    from axon.store.pg_graph_repository import PostgresGraphRepository

                    repo = PostgresGraphRepository(load_runtime_config().pg_url)
                    await repo.ensure_schema()
                    self._graph_repo = repo
        return self._graph_repo

    async def _decisions(self):
        if self._decision_repo is None:
            async with self._repo_init_lock:
                if self._decision_repo is None:
                    from axon.config.runtime import load_runtime_config
                    from axon.store.pg_decision_repository import PostgresDecisionRepository

                    repo = PostgresDecisionRepository(load_runtime_config().pg_url)
                    await repo.ensure_schema()
                    self._decision_repo = repo
        return self._decision_repo

    async def _sessions(self):
        if self._session_repo is None:
            async with self._repo_init_lock:
                if self._session_repo is None:
                    from axon.config.runtime import load_runtime_config
                    from axon.store.pg_session_repository import PostgresSessionRepository

                    repo = PostgresSessionRepository(load_runtime_config().pg_url)
                    await repo.ensure_schema()
                    self._session_repo = repo
        return self._session_repo

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
        repo = await self._decisions()
        return await repo.save_decision(decision)

    async def find_decisions_by_symbol(self, symbol_id: str) -> list[Decision]:
        repo = await self._decisions()
        return await repo.find_decisions_by_symbol(symbol_id)

    async def find_decision_by_git_hash(
        self, git_hash: str, *, repo: str | None = None
    ) -> Decision | None:
        decision_repo = await self._decisions()
        return await decision_repo.find_decision_by_git_hash(git_hash, repo=repo)

    async def find_decisions_by_repo(self, repo: str, limit: int = 20) -> list[Decision]:
        decision_repo = await self._decisions()
        return await decision_repo.find_decisions_by_repo(repo, limit)

    async def latest_decision_ts(self) -> str | None:
        decision_repo = await self._decisions()
        return await decision_repo.latest_decision_ts()

    async def validation_stats(self, *, repo: str | None = None, threshold: float) -> dict:
        decision_repo = await self._decisions()
        return await decision_repo.validation_stats(repo=repo, threshold=threshold)

    async def all_projects(self) -> list[str]:
        decision_repo = await self._decisions()
        return await decision_repo.all_projects()

    async def save_session(
        self, session_id: str, agent: str, repo: str, *, context_payload: str = ""
    ) -> None:
        sess = await self._sessions()
        return await sess.save_session(session_id, agent, repo, context_payload=context_payload)

    async def end_session(self, session_id: str) -> str | None:
        """Mark a session ended; return its repo, or None if the id is unknown."""
        sess = await self._sessions()
        return await sess.end_session(session_id)

    async def next_decision_id(self) -> str:
        """Return the next sequential decision id (dec-NNN, zero-padded)."""
        repo = await self._decisions()
        return await repo.next_decision_id()

    async def close(self) -> None:
        if self._graph_repo is not None and hasattr(self._graph_repo, "close"):
            await self._graph_repo.close()
        if self._decision_repo is not None and hasattr(self._decision_repo, "close"):
            await self._decision_repo.close()
        if self._session_repo is not None and hasattr(self._session_repo, "close"):
            await self._session_repo.close()

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
                await (await self._sessions()).save_code_change_inner(
                    CodeChange(
                        commit_hash=payload["commit_hash"],
                        file_path=payload["file_path"],
                        diff_summary=payload["diff_summary"],
                        why=payload.get("why", ""),
                        changed_at=datetime.fromisoformat(payload["changed_at"]),
                    )
                )
            elif kind == "adr":
                await (await self._decisions()).save_adr_inner(
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

        return await _drain(paths, sink=sink)
