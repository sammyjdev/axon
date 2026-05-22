import json
import logging
from collections import deque

import redis.asyncio as aioredis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class GraphStore:
    """
    Grafo de dependências de código via Redis hashes.

    Estrutura:
        dep:<symbol>  →  { calls: JSON list, called_by: JSON list }

    Busca O(1) por símbolo — não usa vetor, evita ruído semântico.
    """

    def __init__(self, url: str = "redis://localhost:6379") -> None:
        self._redis = aioredis.Redis.from_url(url, decode_responses=True)

    async def connect(self) -> None:
        """Compatibility hook for callers that expect explicit connect."""
        await self._redis.ping()

    async def set_calls(self, symbol: str, calls: list[str]) -> None:
        await self._redis.hset(f"dep:{symbol}", "calls", json.dumps(calls))

    async def set_called_by(self, symbol: str, called_by: list[str]) -> None:
        await self._redis.hset(f"dep:{symbol}", "called_by", json.dumps(called_by))

    async def upsert_deps(
        self,
        symbol: str,
        calls: list[str],
        called_by: list[str],
    ) -> None:
        await self._redis.hset(
            f"dep:{symbol}",
            mapping={
                "calls": json.dumps(calls),
                "called_by": json.dumps(called_by),
            },
        )

    async def get_calls(self, symbol: str) -> list[str]:
        raw = await self._redis.hget(f"dep:{symbol}", "calls")
        return json.loads(raw) if raw else []

    async def get_called_by(self, symbol: str) -> list[str]:
        raw = await self._redis.hget(f"dep:{symbol}", "called_by")
        return json.loads(raw) if raw else []

    async def get_deps(self, symbol: str) -> dict[str, list[str]]:
        data = await self._redis.hgetall(f"dep:{symbol}")
        return {
            "calls": json.loads(data["calls"]) if "calls" in data else [],
            "called_by": json.loads(data["called_by"]) if "called_by" in data else [],
        }

    async def get_subgraph(self, symbol: str) -> dict[str, object]:
        deps = await self.get_deps(symbol)
        return {
            "symbol": symbol,
            "exists": bool(deps["calls"] or deps["called_by"]),
            "calls": deps["calls"],
            "called_by": deps["called_by"],
        }

    async def traverse(
        self,
        symbol: str,
        max_depth: int = 2,
        max_nodes: int = 25,
    ) -> dict[str, object]:
        visited: set[str] = set()
        edges: list[dict[str, str]] = []
        queue: deque[tuple[str, int]] = deque([(symbol, 0)])

        while queue and len(visited) < max_nodes:
            current, depth = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            if depth >= max_depth:
                continue

            calls = await self.get_calls(current)
            for target in calls:
                edges.append({"from": current, "to": target})
                if target not in visited and len(visited) + len(queue) < max_nodes:
                    queue.append((target, depth + 1))

        return {
            "root": symbol,
            "nodes": sorted(visited),
            "edges": edges,
            "max_depth": max_depth,
            "max_nodes": max_nodes,
        }

    async def delete(self, symbol: str) -> None:
        await self._redis.delete(f"dep:{symbol}")

    # ── Subgraph cache ────────────────────────────────────────────────────────
    # These degrade gracefully: if Redis is unavailable the caller continues
    # via SQLite (a warning is logged, no exception is raised).

    async def cache_subgraph(
        self, node_id: str, payload: dict[str, object], ttl: int = 3600
    ) -> None:
        """Cache a subgraph payload; silently skips if Redis is unavailable."""
        try:
            await self._redis.set(f"subgraph:{node_id}", json.dumps(payload), ex=ttl)
        except RedisError as exc:
            logger.warning("cache_subgraph skipped, redis unavailable: %s", exc)

    async def get_cached_subgraph(self, node_id: str) -> dict[str, object] | None:
        """Return a cached subgraph, or None if missing or Redis is unavailable."""
        try:
            raw = await self._redis.get(f"subgraph:{node_id}")
        except RedisError as exc:
            logger.warning("get_cached_subgraph fell back, redis unavailable: %s", exc)
            return None
        return json.loads(raw) if raw else None

    async def invalidate(self, node_id: str) -> None:
        """Drop a cached subgraph; silently skips if Redis is unavailable."""
        try:
            await self._redis.delete(f"subgraph:{node_id}")
        except RedisError as exc:
            logger.warning("invalidate skipped, redis unavailable: %s", exc)

    async def close(self) -> None:
        await self._redis.aclose()
