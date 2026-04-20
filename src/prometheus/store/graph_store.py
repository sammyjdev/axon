import json

import redis.asyncio as aioredis


class GraphStore:
    """
    Grafo de dependências de código via Redis hashes.

    Estrutura:
        dep:<symbol>  →  { calls: JSON list, called_by: JSON list }

    Busca O(1) por símbolo — não usa vetor, evita ruído semântico.
    """

    def __init__(self, url: str = "redis://localhost:6379") -> None:
        self._redis = aioredis.Redis.from_url(url, decode_responses=True)

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

    async def delete(self, symbol: str) -> None:
        await self._redis.delete(f"dep:{symbol}")

    async def close(self) -> None:
        await self._redis.aclose()
