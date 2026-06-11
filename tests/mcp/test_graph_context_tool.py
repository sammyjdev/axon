"""P5.4: AXON serves graph-aware context via GLYPH over the MCP tool surface.

End-to-end over a small fixed graph with a deterministic fake embedder (no model
download), exercising the real GLYPH retrieval path behind the MCP tool.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from pathlib import Path

import pytest

from axon.core.edge import Edge
from axon.mcp import server
from axon.store.session_store import SessionStore

pytest.importorskip("glyph", reason="glyph-kg not installed")


class FakeTelemetry:
    def __init__(self) -> None:
        self.records: list[object] = []

    def append(self, record: object) -> None:
        self.records.append(record)


class FakeEmbedder:
    """Deterministic bag-of-chars embedder — stable anchoring, zero downloads."""

    _DIM = 32

    def embed(self, texts: Sequence[str]) -> list[Sequence[float]]:
        out: list[Sequence[float]] = []
        for text in texts:
            vec = [0.0] * self._DIM
            for ch in text.lower():
                vec[ord(ch) % self._DIM] += 1.0
            out.append(vec)
        return out


@pytest.fixture
async def store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    monkeypatch.setattr(server, "_get_session_store", lambda: s)
    # a fake embedder keeps CI hermetic (the real one downloads a fastembed model)
    monkeypatch.setattr(server, "_get_graph_embedder", lambda: FakeEmbedder())
    yield s
    await s.close()


async def test_get_graph_context_serves_graph_aware_context(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    telemetry = FakeTelemetry()
    monkeypatch.setattr(server, "_COMPRESSION_TELEMETRY", telemetry)

    # fixed graph: controller -> service -> repository
    for nid in ("CampaignController", "CampaignService", "CampaignRepository"):
        await store.add_node(nid, "symbol", label=nid)
    await store.add_edge(
        Edge(source_id="CampaignController", target_id="CampaignService", type="calls")
    )
    await store.add_edge(
        Edge(source_id="CampaignService", target_id="CampaignRepository", type="calls")
    )

    response = await server.get_graph_context(query="CampaignService", token_budget=400)

    # graph-aware: the anchor and a related neighbor appear, with the CALLS relation
    assert "CampaignService" in response
    assert "CampaignRepository" in response
    assert "calls" in response
    # the call was traced like the other graph tools
    assert telemetry.records and telemetry.records[-1].engine == "get_graph_context"


async def test_get_graph_context_empty_graph(store: SessionStore) -> None:
    response = await server.get_graph_context(query="anything")
    assert "Nenhum contexto" in response
