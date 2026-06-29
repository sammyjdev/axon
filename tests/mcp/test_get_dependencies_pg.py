"""dec-121 Phase 2: get_dependencies reads the call-graph from Postgres."""
from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.mcp import server  # noqa: E402
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


async def test_get_dependencies_returns_calls_and_called_by(monkeypatch, store):
    await store.upsert_deps("svc.handler", calls=["svc.commit"], called_by=["svc.route"])
    monkeypatch.setattr(server, "_get_graph_store", lambda: store)

    out = await server.get_dependencies(symbol="svc.handler")

    assert "svc.commit" in out
    assert "svc.route" in out
    assert "svc.handler" in out


async def test_get_dependencies_no_deps_branch(monkeypatch, store):
    monkeypatch.setattr(server, "_get_graph_store", lambda: store)

    out = await server.get_dependencies(symbol="totally.unknown")

    assert "totally.unknown" in out
    assert "Sem dependências" in out


def test_get_graph_store_builds_postgres_symbol_deps(monkeypatch):
    # The factory must construct the Postgres store, not the Redis GraphStore.
    monkeypatch.setattr(server, "_graph_store", None)
    assert isinstance(server._get_graph_store(), PostgresSymbolDeps)
