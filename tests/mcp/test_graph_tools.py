"""Tests for the SQLite-backed graph MCP tools (T4.4)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from axon.core.edge import Edge
from axon.mcp import server
from axon.store.session_store import SessionStore


class FakeTelemetry:
    def __init__(self) -> None:
        self.records: list[object] = []

    def append(self, record: object) -> None:
        self.records.append(record)


@pytest.fixture
async def store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    monkeypatch.setattr(server, "_get_session_store", lambda: s)
    yield s
    await s.close()


async def test_get_graph_neighbors_lists_edges(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    telemetry = FakeTelemetry()
    monkeypatch.setattr(server, "_COMPRESSION_TELEMETRY", telemetry)
    await store.add_edge(
        Edge(source_id="CampaignService", target_id="CampaignRepository", type="calls")
    )

    response = await server.get_graph_neighbors(node="CampaignService", depth=1)

    assert "CampaignService -> CampaignRepository" in response
    assert len(telemetry.records) == 1
    assert telemetry.records[0].caller == "mcp"
    assert telemetry.records[0].engine == "get_graph_neighbors"


async def test_get_graph_neighbors_empty(store: SessionStore) -> None:
    response = await server.get_graph_neighbors(node="Unknown")
    assert "Nenhum vizinho encontrado" in response


async def test_get_graph_path_finds_route(store: SessionStore) -> None:
    await store.add_edge(Edge(source_id="Controller", target_id="Service", type="calls"))
    await store.add_edge(Edge(source_id="Service", target_id="Repository", type="calls"))

    response = await server.get_graph_path(from_node="Controller", to_node="Repository")

    assert response == "Controller -> Service -> Repository"


async def test_get_graph_path_no_route(store: SessionStore) -> None:
    response = await server.get_graph_path(from_node="A", to_node="B")
    assert "Nenhum caminho encontrado" in response
