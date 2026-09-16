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
