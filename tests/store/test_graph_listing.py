"""Read-only full-graph listing used by the GLYPH bridge (P5.2)."""

from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest

from axon.core.edge import Edge
from axon.store.session_store import SessionStore


@pytest.fixture(scope="module")
def pg_dsn():
    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def store(pg_dsn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Isolated per-test Postgres graph via a fresh container + TRUNCATE.
    monkeypatch.setenv("AXON_PG_URL", pg_dsn)
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    await s._graph()  # ensure nodes/edges schema
    con = await asyncpg.connect(pg_dsn)
    await con.execute("TRUNCATE nodes, edges")
    await con.close()
    yield s
    await s.close()


async def test_all_nodes_returns_every_persisted_node(store: SessionStore) -> None:
    await store.add_node("CampaignService", "symbol", label="CampaignService")
    await store.add_node("CampaignRepository", "symbol", label="CampaignRepository")

    nodes = await store.all_nodes()

    by_id = {n["id"]: n for n in nodes}
    assert set(by_id) == {"CampaignService", "CampaignRepository"}
    assert by_id["CampaignService"]["type"] == "symbol"
    assert by_id["CampaignService"]["label"] == "CampaignService"


async def test_all_edges_returns_every_persisted_edge(store: SessionStore) -> None:
    await store.add_edge(
        Edge(source_id="CampaignService", target_id="CampaignRepository", type="calls")
    )
    await store.add_edge(
        Edge(source_id="a/b.py", target_id="a/c.py", type="imports")
    )

    edges = await store.all_edges()

    triples = {(e.source_id, e.target_id, e.type) for e in edges}
    assert triples == {
        ("CampaignService", "CampaignRepository", "calls"),
        ("a/b.py", "a/c.py", "imports"),
    }


async def test_all_nodes_empty_graph(store: SessionStore) -> None:
    assert await store.all_nodes() == []
    assert await store.all_edges() == []
