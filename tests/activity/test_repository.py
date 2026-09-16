from datetime import UTC, datetime

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer

from axon.activity.models import ActivityEvent, SourceCursor
from axon.activity.repository import PostgresActivityRepository


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

async def test_replaying_the_same_spool_item_creates_one_event(repo, pg_pool):
    event = ActivityEvent(
        event_id="evt-1",
        harness="claude-code",
        source_id="src-1",
        session_id="ses-1",
        occurred_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC),
        kind="test",
        content={},
        coverage="full",
        redactions=[]
    )
    
    await repo.upsert_event(event)
    await repo.upsert_event(event)
    
    async with pg_pool.acquire() as con:
        count = await con.fetchval("SELECT count(*) FROM activity_events")
        assert count == 1

async def test_cursor_does_not_advance_when_event_transaction_fails(repo, pg_pool):
    cursor = SourceCursor(
        session_id="ses-1",
        harness="claude-code",
        source_id="src-1",
        fingerprint="F1"
    )
    
    # Force DB error via missing harness
    bad_event = ActivityEvent.model_construct(
        event_id="evt-1",
        harness=None,
        source_id="src-1",
        session_id="ses-1",
        occurred_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC),
        kind="test",
        content={},
        coverage="full",
        redactions=[]
    )
    
    with pytest.raises(Exception):
        await repo.replay_spooled_event(bad_event, cursor, byte_offset=100)
        
    res = await repo.get_cursor(harness="claude-code", source_id="src-1")
    assert res is None

async def test_replaced_source_never_reuses_an_old_offset(repo, pg_pool):
    event1 = ActivityEvent(
        event_id="evt-1",
        harness="claude-code",
        source_id="src-1",
        session_id="ses-1",
        occurred_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC),
        kind="test",
        content={},
        coverage="full",
        redactions=[]
    )
    cursor1 = SourceCursor(
        session_id="ses-1",
        harness="claude-code",
        source_id="src-1",
        fingerprint="A"
    )
    await repo.replay_spooled_event(event1, cursor1, byte_offset=500)
    
    event2 = ActivityEvent(
        event_id="evt-2",
        harness="claude-code",
        source_id="src-1",
        session_id="ses-2",
        occurred_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC),
        kind="test",
        content={},
        coverage="full",
        redactions=[]
    )
    cursor2 = SourceCursor(
        session_id="ses-2",
        harness="claude-code",
        source_id="src-1",
        fingerprint="B"
    )
    await repo.replay_spooled_event(event2, cursor2, byte_offset=10)
    
    res = await repo.get_cursor(harness="claude-code", source_id="src-1")
    assert res is not None
    assert res.fingerprint == "B"
    
    async with pg_pool.acquire() as con:
        offset = await con.fetchval(
            "SELECT byte_offset FROM activity_cursors WHERE harness=$1 AND source_id=$2",
            "claude-code", "src-1"
        )
        assert offset == 10

async def test_100_fixture_events_replayed_twice_leave_100_rows(repo, pg_pool):
    events = [
        ActivityEvent(
            event_id=f"evt-{i}",
            harness="claude-code",
            source_id=f"src-{i}",
            session_id=f"ses-{i}",
            occurred_at=datetime.now(UTC),
            ingested_at=datetime.now(UTC),
            kind="test",
            content={},
            coverage="full",
            redactions=[]
        )
        for i in range(100)
    ]
    
    for e in events:
        await repo.upsert_event(e)
    for e in events:
        await repo.upsert_event(e)
        
    async with pg_pool.acquire() as con:
        count = await con.fetchval("SELECT count(*) FROM activity_events")
        assert count == 100
