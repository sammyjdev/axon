"""dec-121 Phase 3 Task 4: Postgres-backed OutcomeStore (retires outcomes.db)."""
from __future__ import annotations

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.outcome_store import OutcomeRecord, OutcomeStore  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def store(pg_dsn):
    s = OutcomeStore(dsn=pg_dsn)
    await s.init()
    con = await asyncpg.connect(pg_dsn)
    await con.execute("TRUNCATE outcome_record")
    await con.close()
    yield s
    await s.close()


def _rec(project="axon", context="knowledge", outcome="ok", tags=None):
    return OutcomeRecord(
        project=project, context=context, summary="s", outcome=outcome, tags=tags or ["t"],
    )


async def test_save_and_get_outcomes_for_context(store):
    rid = await store.save_outcome(_rec(outcome="kept chunking", tags=["chunker", "review"]))
    outcomes = await store.get_outcomes_for_context("axon", "knowledge")
    assert rid > 0
    assert len(outcomes) == 1
    assert outcomes[0].outcome == "kept chunking"
    assert outcomes[0].tags == ["chunker", "review"]


async def test_get_outcomes_for_context_filters_project(store):
    await store.save_outcome(_rec(project="axon", outcome="rel"))
    await store.save_outcome(_rec(project="other", outcome="irrel"))

    outcomes = await store.get_outcomes_for_context("axon", "knowledge")
    assert len(outcomes) == 1
    assert outcomes[0].project == "axon"


async def test_find_outcomes_by_tag_and_limit(store):
    for i in range(4):
        await store.save_outcome(_rec(context="saas", outcome=f"r{i}", tags=["reuse", "playbook"]))

    outcomes = await store.find_outcomes_by_tag("playbook", project="axon", limit=2)
    assert len(outcomes) == 2
    assert all("playbook" in o.tags for o in outcomes)
