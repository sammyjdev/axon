"""dec-121 Phase 3 Task 1: decision-repo methods for the SQLite-bypass callsites.

latest_decision_ts (replaces __main__.py raw query), validation_stats (replaces
validation/aggregate.py json_extract), all_projects (replaces pb.py adr sync).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.core.decision import Decision  # noqa: E402
from axon.store.pg_decision_repository import PostgresDecisionRepository  # noqa: E402
from axon.store.session_store import ADR  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def repo(pg_dsn):
    r = PostgresDecisionRepository(dsn=pg_dsn)
    await r.ensure_schema()
    # isolate each test
    pool = await r._ensure_pool()
    async with pool.acquire() as con:
        await con.execute("TRUNCATE decisions, adr")
    yield r
    await r.close()


def _dec(did, *, repo, ts, judged, score):
    return Decision(
        id=did, timestamp=ts, agent="manual", repo=repo, summary=did,
        validation_score=score, judged=judged,
    )


async def test_latest_decision_ts_returns_max_created_at(repo):
    await repo.save_decision(_dec("dec-001", repo="axon",
                                  ts=datetime(2026, 1, 1, tzinfo=UTC), judged=True, score=4.0))
    await repo.save_decision(_dec("dec-002", repo="axon",
                                  ts=datetime(2026, 3, 1, tzinfo=UTC), judged=False, score=0.0))
    await repo.save_decision(_dec("dec-003", repo="other",
                                  ts=datetime(2026, 2, 1, tzinfo=UTC), judged=True, score=5.0))

    assert await repo.latest_decision_ts() == datetime(2026, 3, 1, tzinfo=UTC).isoformat()


async def test_latest_decision_ts_none_when_empty(repo):
    assert await repo.latest_decision_ts() is None


async def test_validation_stats_filtered_by_repo(repo):
    await repo.save_decision(_dec("dec-001", repo="axon",
                                  ts=datetime(2026, 1, 1, tzinfo=UTC), judged=True, score=4.0))
    await repo.save_decision(_dec("dec-002", repo="axon",
                                  ts=datetime(2026, 1, 2, tzinfo=UTC), judged=True, score=3.0))
    await repo.save_decision(_dec("dec-003", repo="axon",
                                  ts=datetime(2026, 1, 3, tzinfo=UTC), judged=False, score=0.0))
    await repo.save_decision(_dec("dec-004", repo="other",
                                  ts=datetime(2026, 1, 4, tzinfo=UTC), judged=True, score=5.0))

    stats = await repo.validation_stats(repo="axon", threshold=3.5)
    assert stats == {"n_total": 3, "n_scored": 2, "n_passed": 1}


async def test_validation_stats_unfiltered(repo):
    await repo.save_decision(_dec("dec-001", repo="axon",
                                  ts=datetime(2026, 1, 1, tzinfo=UTC), judged=True, score=4.0))
    await repo.save_decision(_dec("dec-002", repo="other",
                                  ts=datetime(2026, 1, 2, tzinfo=UTC), judged=True, score=5.0))
    await repo.save_decision(_dec("dec-003", repo="other",
                                  ts=datetime(2026, 1, 3, tzinfo=UTC), judged=False, score=0.0))

    stats = await repo.validation_stats(repo=None, threshold=3.5)
    assert stats == {"n_total": 3, "n_scored": 2, "n_passed": 2}


async def test_all_projects_returns_distinct_adr_projects(repo):
    base = dict(title="t", context="c", decision="d", rationale="r")
    await repo.save_adr(ADR(project="axon", **base))
    await repo.save_adr(ADR(project="axon", **{**base, "title": "t2"}))
    await repo.save_adr(ADR(project="lume", **base))

    assert await repo.all_projects() == ["axon", "lume"]


async def test_all_projects_empty(repo):
    assert await repo.all_projects() == []
