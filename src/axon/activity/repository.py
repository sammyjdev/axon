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

    async def record_evidence_link(
        self,
        *,
        target_type: str,
        target_id: str,
        session_id: str,
        turn_id: str | None,
        event_id: str | None,
        relation: str,
    ) -> int:
        if relation not in ("context-delivered", "record-supported"):
            raise ValueError(
                f"relation MUST be 'context-delivered' or 'record-supported', got: {relation}"
            )
            
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                """
                INSERT INTO activity_evidence_links (
                    target_type, target_id, session_id, turn_id, event_id, relation, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, now())
                RETURNING id
                """,
                target_type,
                target_id,
                session_id,
                turn_id,
                event_id,
                relation,
            )
            return row["id"]

    async def get_evidence_links(self, *, target_type: str, target_id: str) -> list[dict]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                """
                SELECT 
                    id, target_type, target_id, session_id, 
                    turn_id, event_id, relation, created_at
                FROM activity_evidence_links
                WHERE target_type = $1 AND target_id = $2
                ORDER BY created_at ASC
                """,
                target_type,
                target_id,
            )
            return [dict(row) for row in rows]

    async def list_sessions(
        self,
        *,
        project: str | None = None,
        harness: str | None = None,
        limit: int,
        cursor: str | None = None
    ) -> tuple[list[ActivitySession], str | None]:
        pool = await self._ensure_pool()
        
        offset = int(cursor) if cursor else 0
        where_clauses = []
        args: list[object] = []
        idx = 1
        
        if project:
            where_clauses.append(f"project = ${idx}")
            args.append(project)
            idx += 1
            
        if harness:
            where_clauses.append(f"harness = ${idx}")
            args.append(harness)
            idx += 1
            
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        args.append(limit + 1)
        limit_idx = idx
        args.append(offset)
        offset_idx = idx + 1
        
        sql = f"""
            SELECT *
            FROM activity_sessions
            WHERE {where_sql}
            ORDER BY updated_at DESC, session_id ASC
            LIMIT ${limit_idx} OFFSET ${offset_idx}
        """  # noqa: S608
        
        async with pool.acquire() as con:
            rows = await con.fetch(sql, *args)
            
        sessions = [ActivitySession(**dict(row)) for row in rows]
        
        next_cursor = None
        if len(sessions) > limit:
            sessions = sessions[:limit]
            next_cursor = str(offset + limit)
            
        return sessions, next_cursor

    async def ingest_batch(
        self,
        sessions: list[ActivitySession],
        events: list[ActivityEvent],
        cursor: SourceCursor,
        *,
        byte_offset: int,
    ) -> int:
        """Store one source's new sessions and events and advance its cursor,
        all or nothing, without a spool file per event. Returns how many events
        were new."""
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await con.executemany(
                    """
                    INSERT INTO activity_sessions (
                        session_id, harness, source_id, project, workspace,
                        parent_session_id, status, coverage
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (session_id) DO UPDATE SET
                        coverage=EXCLUDED.coverage,
                        updated_at=now()
                    """,
                    [
                        (
                            s.session_id, s.harness, s.source_id, s.project,
                            s.workspace, s.parent_session_id, s.status, s.coverage,
                        )
                        for s in sessions
                    ],
                )
                source_ids = list({e.source_id for e in events})
                existing = await con.fetchval(
                    "SELECT count(*) FROM activity_events "
                    "WHERE harness=$1 AND source_id = ANY($2::text[])",
                    cursor.harness,
                    source_ids,
                )
                await con.executemany(
                    """
                    INSERT INTO activity_events (
                        event_id, schema_version, harness, source_id, session_id,
                        turn_id, call_id, parent_session_id, occurred_at, ingested_at,
                        kind, content, outcome, coverage, redactions
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (
                            e.event_id, e.schema_version, e.harness, e.source_id,
                            e.session_id, e.turn_id, e.call_id, e.parent_session_id,
                            e.occurred_at, e.ingested_at, e.kind, e.content,
                            e.outcome, e.coverage, e.redactions,
                        )
                        for e in events
                    ],
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
        return len(source_ids) - existing

    async def search_messages(
        self,
        query: str,
        *,
        project: str | None = None,
        harness: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Messages a person or an agent wrote whose text contains `query`,
        newest first. Harness-injected text (skill bodies, reminders) is left
        out: it repeats across every session and would bury the real hits."""
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                """
                SELECT e.session_id, e.occurred_at, e.harness, s.project,
                       e.content->>'role' AS role, e.content->>'text' AS text
                FROM activity_events e
                LEFT JOIN activity_sessions s ON s.session_id = e.session_id
                WHERE e.kind = 'message'
                  AND coalesce(e.content->>'injected', 'false') <> 'true'
                  AND e.content->>'text' ILIKE '%' || $1 || '%'
                  AND ($2::text IS NULL OR s.project = $2)
                  AND ($3::text IS NULL OR e.harness = $3)
                ORDER BY e.occurred_at DESC
                LIMIT $4
                """,
                query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"),
                project,
                harness,
                limit,
            )
        return [dict(r) for r in rows]
