from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.pg_symbol_deps import PostgresSymbolDeps  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def store(pg_dsn):
    s = PostgresSymbolDeps(dsn=pg_dsn)
    await s.ensure_schema()
    yield s
    await s.close()


async def test_upsert_then_get_subgraph(store):
    await store.upsert_deps("mod.foo", calls=["mod.bar"], called_by=["mod.baz"])
    result = await store.get_subgraph("mod.foo")
    assert result == {
        "exists": True,
        "calls": ["mod.bar"],
        "called_by": ["mod.baz"],
    }


async def test_get_subgraph_unknown_symbol(store):
    result = await store.get_subgraph("does.not.exist")
    assert result == {"exists": False, "calls": [], "called_by": []}


async def test_upsert_overwrites_on_conflict(store):
    await store.upsert_deps("mod.dup", calls=["a"], called_by=["b"])
    await store.upsert_deps("mod.dup", calls=["c"], called_by=["d"])
    result = await store.get_subgraph("mod.dup")
    assert result == {"exists": True, "calls": ["c"], "called_by": ["d"]}
