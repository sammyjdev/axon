"""Postgres-backed symbol dependency store (dec-121 Phase 2).

Ports the live Redis ``dep:<symbol>`` call-graph to a ``symbol_deps`` table.
Exposes the same method names the callers used on the old ``GraphStore`` so the
repoint is mechanical: ``upsert_deps`` / ``get_subgraph`` / ``connect`` / ``close``.

The dead ``subgraph:*`` cache, ``traverse`` and batch helpers from the Redis
store are intentionally not ported (zero production readers).
"""
from __future__ import annotations

import asyncio
import json

import asyncpg


async def _init_conn(conn: asyncpg.Connection) -> None:
    # Return jsonb as Python lists (and accept lists on write).
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


class PostgresSymbolDeps:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(
                        self._dsn, init=_init_conn, min_size=1, max_size=5
                    )
        return self._pool

    async def ensure_schema(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_deps (
                    symbol     text PRIMARY KEY,
                    calls      jsonb NOT NULL DEFAULT '[]',
                    called_by  jsonb NOT NULL DEFAULT '[]'
                )
                """
            )

    async def connect(self) -> None:
        """No-op, kept for parity with the old Redis GraphStore callers."""

    async def upsert_deps(
        self, symbol: str, *, calls: list[str], called_by: list[str]
    ) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO symbol_deps (symbol, calls, called_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (symbol) DO UPDATE SET
                    calls=excluded.calls, called_by=excluded.called_by
                """,
                symbol, calls, called_by,
            )

    async def get_subgraph(self, symbol: str) -> dict[str, object]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT calls, called_by FROM symbol_deps WHERE symbol=$1", symbol
            )
        if row is None:
            return {"exists": False, "calls": [], "called_by": []}
        return {
            "exists": True,
            "calls": row["calls"],
            "called_by": row["called_by"],
        }

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
