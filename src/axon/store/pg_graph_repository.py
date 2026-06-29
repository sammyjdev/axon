"""Postgres-backed GraphRepository (dec-121 step 3, wave 2).

Mirrors SqliteGraphRepository byte-for-byte: add_node upsert, add_edge
insert-if-absent, JSON-text payloads, Python BFS for query_subgraph/
shortest_path (ADR Option A - no recursive CTE), all_nodes/all_edges ordering.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import asyncpg

from axon.core.edge import Edge


class PostgresGraphRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        return self._pool

    async def ensure_schema(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    id         text PRIMARY KEY,
                    type       text NOT NULL,
                    label      text NOT NULL DEFAULT '',
                    payload    text,
                    created_at text NOT NULL,
                    updated_at text NOT NULL
                )
                """
            )
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS edges (
                    source_id  text NOT NULL,
                    target_id  text NOT NULL,
                    type       text NOT NULL,
                    payload    text,
                    created_at text NOT NULL,
                    UNIQUE (source_id, target_id, type)
                )
                """
            )
            await con.execute(
                "CREATE INDEX IF NOT EXISTS ix_edges_source ON edges (source_id)"
            )

    async def add_node(self, node_id, node_type, *, label="", payload=None) -> None:
        now = datetime.now(UTC).isoformat()
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO nodes (id, type, label, payload, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (id) DO UPDATE SET
                    type=excluded.type, label=excluded.label,
                    payload=excluded.payload, updated_at=excluded.updated_at
                """,
                node_id, node_type, label, json.dumps(payload or {}), now, now,
            )

    async def add_edge(self, edge: Edge) -> None:
        now = datetime.now(UTC).isoformat()
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO edges (source_id, target_id, type, payload, created_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (source_id, target_id, type) DO NOTHING
                """,
                edge.source_id, edge.target_id, edge.type,
                json.dumps(edge.payload) if edge.payload is not None else None, now,
            )

    async def get_node(self, node_id: str) -> dict[str, object] | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT id, type, label, payload, created_at, updated_at"
                " FROM nodes WHERE id=$1",
                node_id,
            )
        if row is None:
            return None
        return {
            "id": row["id"], "type": row["type"], "label": row["label"],
            "payload": json.loads(row["payload"]) if row["payload"] else {},
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    async def query_subgraph(self, node_id: str, depth: int = 2) -> dict[str, object]:
        visited: set[str] = {node_id}
        frontier: set[str] = {node_id}
        edges: list[dict[str, str]] = []
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            for _ in range(depth):
                if not frontier:
                    break
                rows = await con.fetch(
                    "SELECT source_id, target_id, type FROM edges"
                    " WHERE source_id = ANY($1::text[])",
                    list(frontier),
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
        edges.sort(key=lambda e: (e["source"], e["target"], e["type"]))
        return {"root": node_id, "nodes": sorted(visited), "edges": edges}

    async def shortest_path(self, from_node, to_node, max_depth: int = 10):
        if from_node == to_node:
            return [from_node]
        visited: set[str] = {from_node}
        parent: dict[str, str] = {}
        frontier: list[str] = [from_node]
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            for _ in range(max_depth):
                if not frontier:
                    break
                rows = await con.fetch(
                    "SELECT source_id, target_id FROM edges WHERE source_id = ANY($1::text[])",
                    list(frontier),
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
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                'SELECT id, type, label, payload FROM nodes ORDER BY id COLLATE "C"'
            )
        return [
            {"id": r["id"], "type": r["type"], "label": r["label"],
             "payload": json.loads(r["payload"]) if r["payload"] else {}}
            for r in rows
        ]

    async def all_edges(self) -> list[Edge]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT source_id, target_id, type, payload FROM edges"
                ' ORDER BY source_id COLLATE "C", target_id COLLATE "C", type COLLATE "C"'
            )
        return [
            Edge(source_id=r["source_id"], target_id=r["target_id"], type=r["type"],
                 payload=json.loads(r["payload"]) if r["payload"] else None)
            for r in rows
        ]

    async def graph_signature(self) -> str:
        """Cheap monotonic fingerprint of the graph for cache invalidation.

        Changes whenever a node/edge is added or a node is updated (add_node
        bumps updated_at; edges are insert-only). Replaces the SQLite WAL mtime
        signal, which is a no-op under Postgres.
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT (SELECT count(*) FROM nodes) AS nc,"
                " (SELECT count(*) FROM edges) AS ec,"
                " (SELECT max(updated_at) FROM nodes) AS mu,"
                " (SELECT max(created_at) FROM edges) AS me"
            )
        return f"{row['nc']}:{row['ec']}:{row['mu']}:{row['me']}"

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
