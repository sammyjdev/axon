"""`recent_sessions` is what `.axon/context.md` is built from: the last sessions of one repo."""

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer

from axon.activity.models import ActivityEvent, ActivitySession
from axon.activity.repository import PostgresActivityRepository

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


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
    r = PostgresActivityRepository(pg_dsn)
    await r.ensure_schema()
    yield r
    await r.close()


async def seed_session(repo, sid, *, project="axon", at=T0, harness="claude-code", parent=None):
    await repo.upsert_session(
        ActivitySession(
            session_id=sid, harness=harness, source_id=f"src-{sid}", project=project,
            workspace="/w/axon", parent_session_id=parent, status="ended", coverage="full",
        )
    )


async def seed_event(repo, sid, n, kind, content, at, turn=None, source=None,
                     harness="claude-code"):
    await repo.upsert_event(
        ActivityEvent(
            event_id=f"{sid}-{n}", harness=harness, source_id=source or f"{sid}-{n}",
            session_id=sid,
            turn_id=turn,
            occurred_at=at, ingested_at=at, kind=kind, content=content, coverage="full",
            redactions=[],
        )
    )


def say(text, role="user", injected=False):
    c = {"role": role, "text": text}
    if injected:
        c["injected"] = True
    return c


async def test_sessions_come_back_newest_first_and_only_for_the_asked_repo(repo):
    await seed_session(repo, "old")
    await seed_event(repo, "old", 1, "message", say("old work"), T0)
    await seed_session(repo, "new")
    await seed_event(repo, "new", 1, "message", say("new work"), T0 + timedelta(days=1))
    await seed_session(repo, "other", project="pharos")
    await seed_event(repo, "other", 1, "message", say("elsewhere"), T0 + timedelta(days=2))

    got = await repo.recent_sessions("axon", limit=10)

    assert [s.session_id for s in got] == ["new", "old"]
    assert got[0].harness == "claude-code"
    assert got[0].last_at == T0 + timedelta(days=1)


async def test_a_session_imported_late_is_not_newer_than_one_worked_on_later(repo):
    # The importer stamps each session's usage snapshot with the import time.
    await seed_session(repo, "old")
    await seed_event(repo, "old", 1, "message", say("old ask"), T0)
    await seed_event(repo, "old", 2, "usage", {"input_tokens": 1}, T0 + timedelta(days=9))
    await seed_session(repo, "new")
    await seed_event(repo, "new", 1, "message", say("new ask"), T0 + timedelta(days=1))

    found = await repo.recent_sessions("axon", limit=3)

    assert [s.session_id for s in found] == ["new", "old"]


async def test_the_limit_keeps_only_the_newest_sessions(repo):
    for i in range(5):
        await seed_session(repo, f"s{i}")
        await seed_event(repo, f"s{i}", 1, "message", say("x"), T0 + timedelta(hours=i))

    got = await repo.recent_sessions("axon", limit=3)

    assert [s.session_id for s in got] == ["s4", "s3", "s2"]


async def test_subagent_sessions_and_empty_sessions_are_left_out(repo):
    await seed_session(repo, "main")
    await seed_event(repo, "main", 1, "message", say("hi"), T0)
    await seed_session(repo, "child", parent="main")
    await seed_event(repo, "child", 1, "message", say("sub task"), T0 + timedelta(hours=1))
    await seed_session(repo, "empty")

    got = await repo.recent_sessions("axon", limit=5)

    assert [s.session_id for s in got] == ["main"]


async def test_a_codex_session_stored_by_the_importer_is_listed(repo):
    from pathlib import Path

    from axon.activity.adapters.codex import parse_codex_session

    parsed = parse_codex_session(Path("tests/fixtures/activity/codex/simple_session.jsonl"))
    sessions = [s.model_copy(update={"project": "axon"}) for s in parsed.sessions]
    offset = int(parsed.cursor.fingerprint.rsplit(":", 1)[1])
    await repo.ingest_batch(sessions, parsed.events, parsed.cursor, byte_offset=offset)

    [recent] = await repo.recent_sessions("axon", limit=3)

    assert recent.harness == "codex"
