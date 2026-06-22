"""Postgres-backed DecisionRepository (dec-121 step 3, wave 3).

decisions.frontmatter is JSONB (GIN-indexed); find_* use native operators.
ADR insert uses RETURNING id (Postgres has no lastrowid). judged/validation_score
live in frontmatter and round-trip as real JSON values. No SQLite-lock fallback.
"""
from __future__ import annotations

import json
from datetime import datetime

import asyncpg

from axon.core.decision import Decision
from axon.store.session_store import ADR


async def _init_conn(conn: asyncpg.Connection) -> None:
    # Return jsonb as Python dicts (and accept dicts on write).
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


class PostgresDecisionRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
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
                CREATE TABLE IF NOT EXISTS decisions (
                    id          text PRIMARY KEY,
                    frontmatter jsonb NOT NULL,
                    body        text,
                    vault_path  text,
                    created_at  text NOT NULL
                )
                """
            )
            await con.execute(
                "CREATE INDEX IF NOT EXISTS ix_decisions_fm ON decisions USING gin (frontmatter)"
            )
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS adr (
                    id         bigserial PRIMARY KEY,
                    project    text NOT NULL,
                    title      text NOT NULL,
                    context    text NOT NULL,
                    decision   text NOT NULL,
                    rationale  text NOT NULL,
                    created_at text NOT NULL
                )
                """
            )

    async def save_decision(self, decision: Decision) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO decisions (id, frontmatter, body, vault_path, created_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (id) DO UPDATE SET
                    frontmatter=excluded.frontmatter, body=excluded.body,
                    vault_path=excluded.vault_path, created_at=excluded.created_at
                """,
                decision.id, decision.model_dump(mode="json"), decision.summary,
                None, decision.timestamp.isoformat(),
            )

    async def find_decisions_by_symbol(self, symbol_id: str) -> list[Decision]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT frontmatter FROM decisions"
                " WHERE EXISTS (SELECT 1 FROM jsonb_array_elements_text(frontmatter->'symbols') v"
                "               WHERE v = $1)"
                " ORDER BY created_at DESC",
                symbol_id,
            )
        return [Decision(**r["frontmatter"]) for r in rows]

    async def find_decision_by_git_hash(
        self, git_hash: str, *, repo: str | None = None
    ) -> Decision | None:
        pool = await self._ensure_pool()
        sql = "SELECT frontmatter FROM decisions WHERE frontmatter->>'git_hash' = $1"
        params: list = [git_hash]
        if repo is not None:
            params.append(repo)
            sql += " AND frontmatter->>'repo' = $2"
        sql += " ORDER BY created_at DESC LIMIT 1"
        async with pool.acquire() as con:
            rows = await con.fetch(sql, *params)
        if not rows:
            return None
        return Decision(**rows[0]["frontmatter"])

    async def find_decisions_by_repo(self, repo: str, limit: int = 20) -> list[Decision]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT frontmatter FROM decisions WHERE frontmatter->>'repo' = $1"
                " ORDER BY created_at DESC LIMIT $2",
                repo, limit,
            )
        return [Decision(**r["frontmatter"]) for r in rows]

    async def all_decisions(self) -> list[Decision]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch("SELECT frontmatter FROM decisions ORDER BY created_at")
        return [Decision(**r["frontmatter"]) for r in rows]

    async def next_decision_id(self) -> str:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            count = await con.fetchval("SELECT count(*) FROM decisions")
        return f"dec-{(count or 0) + 1:03d}"

    async def save_adr_inner(self, adr: ADR) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            return await con.fetchval(
                "INSERT INTO adr (project, title, context, decision, rationale, created_at)"
                " VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                adr.project, adr.title, adr.context, adr.decision, adr.rationale,
                adr.created_at.isoformat(),
            )

    async def save_adr(self, adr: ADR) -> int:
        # No SQLite-lock fallback on Postgres - a direct insert.
        return await self.save_adr_inner(adr)

    async def get_adrs(self, project: str, limit: int = 10) -> list[ADR]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, project, title, context, decision, rationale, created_at"
                " FROM adr WHERE project=$1 ORDER BY created_at DESC LIMIT $2",
                project, limit,
            )
        return [
            ADR(
                id=r["id"], project=r["project"], title=r["title"], context=r["context"],
                decision=r["decision"], rationale=r["rationale"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
