"""GraphRepository Protocol and SqliteGraphRepository implementation.

Extracted verbatim from SessionStore (dec-121 step 2, wave 2).
SqliteGraphRepository shares the SessionStore connection and lock;
it does not own them.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import aiosqlite

from axon.core.edge import Edge


@runtime_checkable
class GraphRepository(Protocol):
    """Protocol satisfied by SqliteGraphRepository and PostgresGraphRepository."""

    async def add_node(
        self,
        node_id: str,
        node_type: str,
        *,
        label: str = "",
        payload: dict | None = None,
    ) -> None: ...

    async def add_edge(self, edge: Edge) -> None: ...

    async def get_node(self, node_id: str) -> dict[str, object] | None: ...

    async def query_subgraph(
        self, node_id: str, depth: int = 2
    ) -> dict[str, object]: ...

    async def shortest_path(
        self, from_node: str, to_node: str, max_depth: int = 10
    ) -> list[str] | None: ...

    async def all_nodes(self) -> list[dict[str, object]]: ...

    async def all_edges(self) -> list[Edge]: ...


class SqliteGraphRepository:
    """The original SessionStore graph SQL, sharing the session's connection+lock."""

    def __init__(self, session) -> None:
        self._session = session  # SessionStore; uses _connection() and _lock

    async def add_node(
        self,
        node_id: str,
        node_type: str,
        *,
        label: str = "",
        payload: dict | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        async with self._session._lock:
            db = await self._session._connection()
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
        async with self._session._lock:
            db = await self._session._connection()
            await db.execute(
                "INSERT OR IGNORE INTO edges"
                " (source_id, target_id, type, payload, created_at)"
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
        async with self._session._lock:
            db = await self._session._connection()
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
        async with self._session._lock:
            db = await self._session._connection()
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
        async with self._session._lock:
            db = await self._session._connection()
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

    async def all_nodes(self) -> list[dict[str, object]]:
        """Return every persisted node (id/type/label/payload) for graph export.

        Read-only full scan used by the GLYPH bridge, which needs the whole
        graph at once rather than a seed-anchored subgraph.
        """
        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT id, type, label, payload FROM nodes ORDER BY id"
            )
        return [
            {
                "id": r["id"],
                "type": r["type"],
                "label": r["label"],
                "payload": json.loads(r["payload"]) if r["payload"] else {},
            }
            for r in rows
        ]

    async def all_edges(self) -> list[Edge]:
        """Return every persisted edge as :class:`Edge` for graph export."""
        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT source_id, target_id, type, payload FROM edges"
                " ORDER BY source_id, target_id, type"
            )
        return [
            Edge(
                source_id=r["source_id"],
                target_id=r["target_id"],
                type=r["type"],
                payload=json.loads(r["payload"]) if r["payload"] else None,
            )
            for r in rows
        ]
