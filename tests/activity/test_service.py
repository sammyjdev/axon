from datetime import UTC, datetime

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer

from axon.activity.models import ActivityEvent, ActivityFilters, ActivitySession, SourceCursor
from axon.activity.repository import PostgresActivityRepository
from axon.activity.service import ActivityService
from axon.activity.spool import spool_event


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"  # noqa: S106
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")

@pytest.fixture
async def pg_pool(pg_dsn):
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        yield pool
    finally:
        await pool.close()

@pytest.fixture
async def repo(pg_dsn, pg_pool):
    async with pg_pool.acquire() as con:
        await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    
    repo = PostgresActivityRepository(pg_dsn)
    await repo.ensure_schema()
    yield repo
    await repo.close()

@pytest.fixture
def isolate_spool(monkeypatch, tmp_path):
    monkeypatch.setattr("axon.activity.spool.data_root", lambda: tmp_path)
    return tmp_path

@pytest.fixture
def service(repo, isolate_spool):
    return ActivityService(repo)

async def test_export_import_preserves_ids_content_and_relationships(service, pg_pool):
    events = [
        ActivityEvent(
            event_id=f"evt-{i}",
            harness="claude-code",
            source_id=f"src-{i}",
            session_id="ses-1",
            parent_session_id="ses-parent",
            occurred_at=datetime.now(UTC),
            ingested_at=datetime.now(UTC),
            kind="test",
            content={"msg": f"hello {i}"},
            coverage="full",
            redactions=[]
        ) for i in range(3)
    ]
    cursor = SourceCursor(
        session_id="ses-1",
        harness="claude-code",
        source_id="src-1",
        fingerprint="F1"
    )
    
    await service.ingest_events(events, cursor=cursor)
    
    exported_lines = [line async for line in service.export_activity("ses-1")]
    assert len(exported_lines) == 3
    
    # reset schema
    async with pg_pool.acquire() as con:
        await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await service.repo.ensure_schema()
    
    res = await service.import_events(exported_lines)
    assert res.stored == 3
    
    timeline = await service.get_session_timeline("ses-1")
    assert len(timeline) == 3
    
    # Check attributes
    for i, e in enumerate(timeline):
        assert e.event_id == f"evt-{i}"
        assert e.content == {"msg": f"hello {i}"}
        assert e.session_id == "ses-1"
        assert e.parent_session_id == "ses-parent"

async def test_deleted_source_log_does_not_advance_dependency_on_the_source(service, isolate_spool):
    event = ActivityEvent(
        event_id="evt-1",
        harness="claude-code",
        source_id="src-1",
        session_id="ses-1",
        occurred_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC),
        kind="test",
        content={"data": "x"},
        coverage="full",
        redactions=[]
    )
    cursor = SourceCursor(
        session_id="ses-1",
        harness="claude-code",
        source_id="src-1",
        fingerprint="F1"
    )
    await service.ingest_events([event], cursor=cursor)
    
    # Delete the spool explicitly to prove we don't need it
    import shutil
    shutil.rmtree(isolate_spool)
    
    timeline = await service.get_session_timeline("ses-1")
    assert len(timeline) == 1
    assert timeline[0].event_id == "evt-1"
    
    exported = [line async for line in service.export_activity("ses-1")]
    assert len(exported) == 1

async def test_search_does_not_cross_project_or_harness_filter(service, pg_pool):
    # Setup two sessions in different projects and harnesses
    s1 = ActivitySession(
        session_id="ses-1", harness="claude-code", source_id="src-1", project="proj-A",
        workspace=None, status="ok", coverage="full",
    )
    s2 = ActivitySession(
        session_id="ses-2", harness="agy", source_id="src-2", project="proj-B",
        workspace=None, status="ok", coverage="full",
    )
    await service.repo.upsert_session(s1)
    await service.repo.upsert_session(s2)
    
    e1 = ActivityEvent(
        event_id="evt-1", harness="claude-code", source_id="src-1", session_id="ses-1",
        occurred_at=datetime.now(UTC), ingested_at=datetime.now(UTC),
        kind="msg", content={"text": "shared_keyword"}, coverage="full", redactions=[]
    )
    e2 = ActivityEvent(
        event_id="evt-2", harness="agy", source_id="src-2", session_id="ses-2",
        occurred_at=datetime.now(UTC), ingested_at=datetime.now(UTC),
        kind="msg", content={"text": "shared_keyword"}, coverage="full", redactions=[]
    )
    
    await service.repo.upsert_event(e1)
    await service.repo.upsert_event(e2)
    
    # Search with proj-A and claude-code
    filters = ActivityFilters(project="proj-A", harness="claude-code")
    page = await service.search_activity("shared_keyword", filters=filters, limit=10, cursor=None)
    
    assert len(page.events) == 1
    assert page.events[0].event_id == "evt-1"
    
    # Search with proj-B and agy
    filters2 = ActivityFilters(project="proj-B", harness="agy")
    page2 = await service.search_activity("shared_keyword", filters=filters2, limit=10, cursor=None)
    
    assert len(page2.events) == 1
    assert page2.events[0].event_id == "evt-2"

    # Same project, different harness: proves the harness filter alone
    # isolates results, independent of the project filter.
    s3 = ActivitySession(
        session_id="ses-3", harness="agy", source_id="src-3", project="proj-A",
        workspace=None, status="ok", coverage="full",
    )
    await service.repo.upsert_session(s3)
    e3 = ActivityEvent(
        event_id="evt-3", harness="agy", source_id="src-3", session_id="ses-3",
        occurred_at=datetime.now(UTC), ingested_at=datetime.now(UTC),
        kind="msg", content={"text": "shared_keyword"}, coverage="full", redactions=[],
    )
    await service.repo.upsert_event(e3)

    page3 = await service.search_activity(
        "shared_keyword", filters=ActivityFilters(project="proj-A", harness="claude-code"),
        limit=10, cursor=None,
    )
    assert [e.event_id for e in page3.events] == ["evt-1"]

async def test_health_reports_pending_spool_after_database_outage(service, isolate_spool):
    event = ActivityEvent(
        event_id="evt-1", harness="claude-code", source_id="src-1", session_id="ses-1",
        occurred_at=datetime.now(UTC), ingested_at=datetime.now(UTC),
        kind="test", content={}, coverage="full", redactions=[]
    )
    
    # Just spool the event (simulating an outage where drain fails)
    await spool_event(event.model_dump(mode="json"))
    
    health = await service.health()
    assert health.pending_count >= 1
    assert health.pending_bytes > 0


async def test_ingest_events_persists_sessions_durably(service, pg_pool):
    """Review finding 1: every adapter fills AdapterResult.sessions, but
    nothing durably persisted them - activity_sessions stayed empty in every
    real deployment. This goes through ActivityService.ingest_events (the
    same path the CLI uses), never repo.upsert_session directly."""
    session = ActivitySession(
        session_id="ses-durable",
        harness="claude-code",
        source_id="src-durable",
        project="proj-durable",
        workspace="/repo",
        status="observed",
        coverage="complete-observable",
    )
    event = ActivityEvent(
        event_id="evt-durable", harness="claude-code", source_id="src-durable",
        session_id="ses-durable", occurred_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC), kind="test", content={},
        coverage="full", redactions=[],
    )
    cursor = SourceCursor(
        session_id="ses-durable", harness="claude-code", source_id="src-durable",
        fingerprint="fp-durable",
    )

    await service.ingest_events([event], cursor=cursor, sessions=[session])

    async with pg_pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT session_id, project, coverage FROM activity_sessions WHERE session_id=$1",
            "ses-durable",
        )
    assert row is not None
    assert row["project"] == "proj-durable"
    assert row["coverage"] == "complete-observable"


async def test_spool_contamination_across_harnesses_is_prevented(service, pg_pool):
    """Review finding 3: ingest_events drains the single shared spool dir,
    and the old sink closed over the CURRENT call's cursor for every item it
    swept - including another harness's leftover item from an earlier failed
    call. Each spooled item must carry its own cursor identity so a later
    call's drain never contaminates a different source's cursor row."""
    event_a = ActivityEvent(
        event_id="evt-a", harness="claude-code", source_id="src-a",
        session_id="ses-a", occurred_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC), kind="test", content={},
        coverage="full", redactions=[],
    )
    cursor_a = SourceCursor(
        session_id="ses-a", harness="claude-code", source_id="src-a", fingerprint="fp-a"
    )

    # First ingest fails (simulated outage): the event stays in the shared
    # spool, unprocessed.
    real_replay = service.repo.replay_spooled_event

    async def failing_replay(*args, **kwargs):
        raise OSError("simulated outage")

    service.repo.replay_spooled_event = failing_replay
    res1 = await service.ingest_events([event_a], cursor=cursor_a, byte_offset=111)
    assert res1.stored == 0
    service.repo.replay_spooled_event = real_replay

    # Second ingest, a DIFFERENT harness/source, succeeds - and its drain
    # sweeps up event_a's leftover spool item too (same shared spool dir).
    event_b = ActivityEvent(
        event_id="evt-b", harness="codex", source_id="src-b",
        session_id="ses-b", occurred_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC), kind="test", content={},
        coverage="full", redactions=[],
    )
    cursor_b = SourceCursor(
        session_id="ses-b", harness="codex", source_id="src-b", fingerprint="fp-b"
    )
    res2 = await service.ingest_events([event_b], cursor=cursor_b, byte_offset=222)
    assert res2.stored == 2  # event_a (now succeeding) + event_b

    cursor_row_a = await service.repo.get_cursor(harness="claude-code", source_id="src-a")
    cursor_row_b = await service.repo.get_cursor(harness="codex", source_id="src-b")
    assert cursor_row_a is not None
    assert cursor_row_a.fingerprint == "fp-a"
    assert cursor_row_b is not None
    assert cursor_row_b.fingerprint == "fp-b"

    async with pg_pool.acquire() as con:
        offset_a = await con.fetchval(
            "SELECT byte_offset FROM activity_cursors WHERE harness=$1 AND source_id=$2",
            "claude-code", "src-a",
        )
        offset_b = await con.fetchval(
            "SELECT byte_offset FROM activity_cursors WHERE harness=$1 AND source_id=$2",
            "codex", "src-b",
        )
    assert offset_a == 111
    assert offset_b == 222
