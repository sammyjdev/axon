"""Read-only full-graph listing used by the GLYPH bridge (P5.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from axon.core.edge import Edge
from axon.store.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # These tests use an isolated per-test SQLite graph; pin the backend so they
    # do not route to the shared postgres after the wave-2 cutover flip.
    monkeypatch.setenv("AXON_GRAPH_BACKEND", "sqlite")
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
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
