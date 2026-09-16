# ruff: noqa: S108
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer

from axon.activity.adapters.agy import build_agy_session
from axon.activity.adapters.claude_code import parse_claude_code_session
from axon.activity.adapters.codex import parse_codex_session
from axon.activity.agy_runner import AgyRunResult
from axon.activity.repository import PostgresActivityRepository
from axon.activity.service import ActivityService


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon" # noqa: S106
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
def service(repo):
    return ActivityService(repo)

async def test_controlled_harness_fixture_matches_timeline_observations(service, repo, pg_pool):
    # Parse Claude Code
    cc_path = Path("tests/fixtures/activity/claude_code/simple_session.jsonl")
    cc_res = parse_claude_code_session(cc_path)
    
    # Parse Codex
    cx_path = Path("tests/fixtures/activity/codex/simple_session.jsonl")
    cx_res = parse_codex_session(cx_path)
    
    # Build AGY
    agy_res = AgyRunResult(
        prompt="do something",
        output="ok",
        exit_code=0,
        timed_out=False,
        workspace=Path("/tmp/workspace"),
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC)
    )
    agy_adapter_res = build_agy_session(agy_res, source_id="agy-src-1")

    all_events = cc_res.events + cx_res.events + agy_adapter_res.events
    await service.ingest_events(cc_res.events, cursor=cc_res.cursor)
    await service.ingest_events(cx_res.events, cursor=cx_res.cursor)
    await service.ingest_events(agy_adapter_res.events, cursor=agy_adapter_res.cursor)
    
    # Get all distinct session IDs from events
    session_ids = set(e.session_id for e in all_events)
    for sid in session_ids:
        timeline = await service.get_session_timeline(sid)
        expected_events = [e for e in all_events if e.session_id == sid]
        
        assert len(timeline) == len(expected_events)
        
        # Sort by occurred_at for comparison
        timeline.sort(key=lambda x: (x.occurred_at, x.event_id))
        expected_events.sort(key=lambda x: (x.occurred_at, x.event_id))
        
        for t_evt, e_evt in zip(timeline, expected_events):
            assert t_evt.kind == e_evt.kind
            assert t_evt.content == e_evt.content
            assert t_evt.coverage == e_evt.coverage

async def test_backup_restore_preserves_activity_export_identity(service, repo, pg_pool):
    # Setup data
    cc_path = Path("tests/fixtures/activity/claude_code/simple_session.jsonl")
    cc_res = parse_claude_code_session(cc_path)
    
    cx_path = Path("tests/fixtures/activity/codex/simple_session.jsonl")
    cx_res = parse_codex_session(cx_path)
    
    agy_res = AgyRunResult(
        prompt="backup restore",
        output="ok",
        exit_code=0,
        timed_out=False,
        workspace=Path("/tmp/workspace"),
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC)
    )
    agy_adapter_res = build_agy_session(agy_res, source_id="agy-src-2")

    all_events = cc_res.events + cx_res.events + agy_adapter_res.events
    await service.ingest_events(cc_res.events, cursor=cc_res.cursor)
    await service.ingest_events(cx_res.events, cursor=cx_res.cursor)
    await service.ingest_events(agy_adapter_res.events, cursor=agy_adapter_res.cursor)

    # Export
    exported_lines = []
    # For every session id, export activity.
    session_ids = set(e.session_id for e in all_events)
    for sid in session_ids:
        async for line in service.export_activity(sid):
            exported_lines.append(line)
            
    # Simulate DB restore
    async with pg_pool.acquire() as con:
        await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await repo.ensure_schema()
    
    # Import
    res = await service.import_events(exported_lines)
    assert res.stored > 0
    
    # Assert
    for sid in session_ids:
        timeline = await service.get_session_timeline(sid)
        expected_events = [e for e in all_events if e.session_id == sid]
        assert len(timeline) == len(expected_events)
        
        timeline.sort(key=lambda x: (x.occurred_at, x.event_id))
        expected_events.sort(key=lambda x: (x.occurred_at, x.event_id))
        
        for t_evt, e_evt in zip(timeline, expected_events):
            assert t_evt.event_id == e_evt.event_id
            assert t_evt.content == e_evt.content
            assert t_evt.coverage == e_evt.coverage
