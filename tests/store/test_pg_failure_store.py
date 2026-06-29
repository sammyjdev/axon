"""dec-121 Phase 3 Task 3: Postgres-backed FailureStore (retires failures.db)."""
from __future__ import annotations

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.failure_store import FailureRecord, FailureStore  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def store(pg_dsn):
    s = FailureStore(dsn=pg_dsn)
    await s.init()
    con = await asyncpg.connect(pg_dsn)
    await con.execute("TRUNCATE failure_record")
    await con.close()
    yield s
    await s.close()


def _rec(project="axon", operation="op", cause="cause", tags=None):
    return FailureRecord(
        project=project, operation=operation, error_message="boom",
        probable_cause=cause, tags=tags or ["t"],
    )


async def test_save_and_get_recent_failures(store):
    rid = await store.save_failure(_rec(cause="dup threshold low", tags=["til", "promotion"]))
    failures = await store.get_recent_failures("axon")
    assert rid > 0
    assert len(failures) == 1
    assert failures[0].probable_cause == "dup threshold low"
    assert failures[0].tags == ["til", "promotion"]


async def test_get_recent_failures_respects_project_and_limit(store):
    for i in range(4):
        await store.save_failure(_rec(operation=f"task-{i}", cause="shared", tags=["shared"]))
    await store.save_failure(_rec(project="other", cause="other", tags=["shared"]))

    failures = await store.get_recent_failures("axon", limit=3)
    assert len(failures) == 3
    assert all(f.project == "axon" for f in failures)


async def test_find_failures_by_tag_filters_project(store):
    await store.save_failure(_rec(project="axon", tags=["io", "retry"]))
    await store.save_failure(_rec(project="other", tags=["io", "retry"]))

    failures = await store.find_failures_by_tag("retry", project="axon")
    assert len(failures) == 1
    assert failures[0].project == "axon"


async def test_get_repeated_failures_groups_by_probable_cause(store):
    await store.save_failure(_rec(cause="network jitter", tags=["io"]))
    await store.save_failure(_rec(cause="network jitter", tags=["io"]))
    await store.save_failure(_rec(cause="bad dedupe", tags=["cfg"]))

    repeated = await store.get_repeated_failures("axon", min_occurrences=2)
    assert repeated == [("network jitter", 2)]
