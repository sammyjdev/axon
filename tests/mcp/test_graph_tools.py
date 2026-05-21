from __future__ import annotations

import pytest

from axon.mcp import server


class FakeTelemetry:
    def __init__(self) -> None:
        self.records = []

    def append(self, record) -> None:
        self.records.append(record)


@pytest.mark.asyncio
async def test_get_graph_neighbors_records_mcp_compression_record(monkeypatch) -> None:
    telemetry = FakeTelemetry()
    captured: dict[str, str] = {}

    async def fake_read(query: str):
        captured["query"] = query
        return [{"node": "CampaignService", "neighbor": "CampaignRepository"}]

    monkeypatch.setattr(server, "_COMPRESSION_TELEMETRY", telemetry)
    monkeypatch.setattr(server, "_run_neo4j_read", fake_read)

    response = await server.get_graph_neighbors(
        node="CampaignService",
        project="rpg-master-ai",
        depth=2,
    )

    assert "CampaignRepository" in response
    assert "rpg_master_ai__" in captured["query"]
    assert len(telemetry.records) == 1
    assert telemetry.records[0].caller == "mcp"
    assert telemetry.records[0].engine == "get_graph_neighbors"


@pytest.mark.asyncio
async def test_get_graph_path_uses_project_namespace(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_read(query: str):
        captured["query"] = query
        return [{"path": ["Controller", "Service"]}]

    monkeypatch.setattr(server, "_run_neo4j_read", fake_read)

    response = await server.get_graph_path(
        from_node="Controller",
        to_node="Service",
        project="prometheus",
    )

    assert "Controller" in response
    assert "Service" in response
    assert "STARTS WITH 'prometheus__'" in captured["query"]
