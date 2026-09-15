import asyncio
import json

import asyncpg

from axon.activity.models import ActivityEvent, ActivitySession, SourceCursor
from axon.store.pg_migrations import PG_MIGRATIONS_DIR, apply_pg_migrations


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


class PostgresActivityRepository:
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
            await apply_pg_migrations(con, PG_MIGRATIONS_DIR)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def upsert_session(self, session: ActivitySession) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO activity_sessions (
                    session_id, harness, source_id, project, workspace,
                    parent_session_id, status, coverage
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (harness, source_id) DO UPDATE SET
                    status=EXCLUDED.status,
                    coverage=EXCLUDED.coverage,
                    updated_at=now()
                """,
                session.session_id,
                session.harness,
                session.source_id,
                session.project,
                session.workspace,
                session.parent_session_id,
                session.status,
                session.coverage,
            )

    async def upsert_event(self, event: ActivityEvent) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO activity_events (
                    event_id, schema_version, harness, source_id, session_id,
                    turn_id, call_id, parent_session_id, occurred_at, ingested_at,
                    kind, content, outcome, coverage, redactions
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15
                )
                ON CONFLICT (harness, source_id) DO NOTHING
                """,
                event.event_id,
                event.schema_version,
                event.harness,
                event.source_id,
                event.session_id,
                event.turn_id,
                event.call_id,
                event.parent_session_id,
                event.occurred_at,
                event.ingested_at,
                event.kind,
                event.content,
                event.outcome,
                event.coverage,
                event.redactions,
            )

    async def get_cursor(self, *, harness: str, source_id: str) -> SourceCursor | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT harness, source_id, fingerprint FROM activity_cursors "
                "WHERE harness=$1 AND source_id=$2",
                harness,
                source_id,
            )
        if row is None:
            return None
        return SourceCursor(
            session_id="",  # Not stored in activity_cursors
            harness=row["harness"], # type: ignore
            source_id=row["source_id"],
            fingerprint=row["fingerprint"],
        )

    async def replay_spooled_event(
        self, event: ActivityEvent, cursor: SourceCursor, *, byte_offset: int
    ) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await con.execute(
                    """
                    INSERT INTO activity_events (
                        event_id, schema_version, harness, source_id, session_id,
                        turn_id, call_id, parent_session_id, occurred_at, ingested_at,
                        kind, content, outcome, coverage, redactions
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15
                    )
                    ON CONFLICT (harness, source_id) DO NOTHING
                    """,
                    event.event_id,
                    event.schema_version,
                    event.harness,
                    event.source_id,
                    event.session_id,
                    event.turn_id,
                    event.call_id,
                    event.parent_session_id,
                    event.occurred_at,
                    event.ingested_at,
                    event.kind,
                    event.content,
                    event.outcome,
                    event.coverage,
                    event.redactions,
                )
                await con.execute(
                    """
                    INSERT INTO activity_cursors (harness, source_id, fingerprint, byte_offset)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (harness, source_id) DO UPDATE SET
                        fingerprint=EXCLUDED.fingerprint,
                        byte_offset=EXCLUDED.byte_offset,
                        updated_at=now()
                    """,
                    cursor.harness,
                    cursor.source_id,
                    cursor.fingerprint,
                    byte_offset,
                )
