import json
import logging
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass

import asyncpg

from axon.activity.export import event_to_jsonl_line, jsonl_line_to_event
from axon.activity.models import ActivityEvent, ActivityFilters, ActivityPage, SourceCursor
from axon.activity.repository import PostgresActivityRepository
from axon.activity.spool import activity_spool_paths, drain_spool, spool_event

logger = logging.getLogger(__name__)

@dataclass
class IngestResult:
    spooled: int
    stored: int
    warnings: list[str]

@dataclass
class HealthReport:
    pending_count: int
    pending_bytes: int
    stored_bytes: int | None
    latest_error: str | None
    compatibility_warnings: list[str]

class ActivityService:
    def __init__(self, repo: PostgresActivityRepository) -> None:
        self.repo = repo

    async def ingest_events(
        self, events: Sequence[ActivityEvent], *, cursor: SourceCursor, byte_offset: int = 0
    ) -> IngestResult:
        """Spool events durably, then drain the shared spool.

        The spool directory is shared by every caller of this method (one
        process-wide pending queue), so a drain triggered by THIS call can
        sweep up items a different, earlier call spooled and never finished
        draining (e.g. after a transient DB outage). Each spooled item
        therefore carries its OWN cursor/byte_offset (embedded at spool
        time), never the enclosing call's `cursor`/`byte_offset` closed over
        by the sink - otherwise a leftover item from another source would be
        replayed under the wrong (harness, source_id) cursor identity, or
        this call's cursor would be stamped onto an unrelated source.
        """
        spooled = 0
        warnings: list[str] = []
        cursor_payload = cursor.model_dump(mode="json")

        for event in events:
            await spool_event(
                {
                    "cursor": cursor_payload,
                    "byte_offset": byte_offset,
                    "data": event.model_dump(mode="json"),
                },
                commit_hash=event.event_id,
            )
            spooled += 1

        def is_retryable(e: Exception) -> bool:
            if isinstance(e, asyncpg.IntegrityConstraintViolationError):
                return False
            return isinstance(e, (asyncpg.PostgresError, OSError))

        async def sink(payload: dict) -> None:
            ev = ActivityEvent.model_validate(payload["data"])
            item_cursor = SourceCursor.model_validate(payload["cursor"])
            item_byte_offset = payload["byte_offset"]
            await self.repo.replay_spooled_event(ev, item_cursor, byte_offset=item_byte_offset)

        res = await drain_spool(
            None,
            sink=sink,
            is_retryable=is_retryable
        )

        stored = res.processed
        if res.quarantined:
            warnings.append(f"{res.quarantined} events were quarantined during drain.")

        return IngestResult(spooled=spooled, stored=stored, warnings=warnings)

    async def get_session_timeline(self, session_id: str) -> list[ActivityEvent]:
        pool = await self.repo._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM activity_events WHERE session_id=$1 ORDER BY occurred_at",
                session_id
            )
        return [ActivityEvent(**dict(row)) for row in rows]

    async def search_activity(
        self, query: str, *, filters: ActivityFilters, limit: int, cursor: str | None
    ) -> ActivityPage:
        pool = await self.repo._ensure_pool()

        offset = int(cursor) if cursor else 0

        where_clauses = ["e.content::text ILIKE $1"]
        args: list[object] = [f"%{query}%"]
        idx = 2

        if filters.project:
            where_clauses.append(f"s.project = ${idx}")
            args.append(filters.project)
            idx += 1

        if filters.harness:
            where_clauses.append(f"e.harness = ${idx}")
            args.append(filters.harness)
            idx += 1

        if filters.date_from:
            where_clauses.append(f"e.occurred_at >= ${idx}")
            args.append(filters.date_from)
            idx += 1

        if filters.date_to:
            where_clauses.append(f"e.occurred_at <= ${idx}")
            args.append(filters.date_to)
            idx += 1

        if filters.outcome:
            where_clauses.append(f"e.outcome = ${idx}")
            args.append(filters.outcome)
            idx += 1

        where_sql = " AND ".join(where_clauses)

        args.append(limit + 1)
        limit_idx = idx
        args.append(offset)
        offset_idx = idx + 1

        sql = f"""
            SELECT e.*
            FROM activity_events e
            LEFT JOIN activity_sessions s ON e.session_id = s.session_id
            WHERE {where_sql}
            ORDER BY e.occurred_at DESC, e.event_id ASC
            LIMIT ${limit_idx} OFFSET ${offset_idx}
        """  # noqa: S608

        async with pool.acquire() as con:
            rows = await con.fetch(sql, *args)

        events = [ActivityEvent(**dict(row)) for row in rows]

        next_cursor = None
        if len(events) > limit:
            events = events[:limit]
            next_cursor = str(offset + limit)

        return ActivityPage(events=events, next_cursor=next_cursor)

    async def export_activity(self, session_id: str) -> AsyncIterator[str]:
        pool = await self.repo._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM activity_events WHERE session_id=$1 ORDER BY occurred_at",
                session_id
            )
            for row in rows:
                event = ActivityEvent(**dict(row))
                yield event_to_jsonl_line(event)

    async def import_events(self, lines: Iterable[str]) -> IngestResult:
        stored = 0
        spooled = 0
        warnings = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            event = jsonl_line_to_event(line)
            await self.repo.upsert_event(event)
            stored += 1
        return IngestResult(spooled=spooled, stored=stored, warnings=warnings)

    async def health(self) -> HealthReport:
        paths = activity_spool_paths()
        pending_count = 0
        pending_bytes = 0
        if paths.pending_dir.exists():
            for f in paths.pending_dir.iterdir():
                if f.is_file():
                    pending_count += 1
                    pending_bytes += f.stat().st_size

        latest_error = None
        if paths.quarantine_log.exists():
            try:
                # Read the last line for the latest error
                lines = paths.quarantine_log.read_text().splitlines()
                if lines:
                    last_line = json.loads(lines[-1])
                    latest_error = last_line.get("reason")
            except Exception as e:
                logger.warning("Could not read quarantine log: %s", e)

        stored_bytes = None
        try:
            pool = await self.repo._ensure_pool()
            async with pool.acquire() as con:
                # Use a safe query to get table size
                size = await con.fetchval(
                    "SELECT pg_total_relation_size('activity_events')"
                )
                if size is not None:
                    stored_bytes = int(size)
        except Exception as e:
            logger.warning("Could not query stored_bytes: %s", e)

        return HealthReport(
            pending_count=pending_count,
            pending_bytes=pending_bytes,
            stored_bytes=stored_bytes,
            latest_error=latest_error,
            compatibility_warnings=[],
        )
