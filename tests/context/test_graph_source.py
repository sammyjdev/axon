"""GLYPH-backed GraphContextSource: type mapping + ContextPack adaptation (P5.2)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from axon.context.contracts import ContextPack
from axon.context.graph_source import (
    GlyphEmbedderAdapter,
    GraphContextSource,
    map_edge_type,
    map_node_type,
)
from axon.core.edge import Edge
from axon.store.session_store import SessionStore

try:  # the glyph enums are the assertion target; skip cleanly if lib is absent
    from glyph.model.edge import EdgeType as GlyphEdgeType
    from glyph.model.node import NodeType as GlyphNodeType
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip("glyph-kg not installed", allow_module_level=True)


class FakeEmbedder:
    """Deterministic bag-of-chars embedder — no model download, stable anchoring."""

    _DIM = 32

    def embed(self, texts: Sequence[str]) -> list[Sequence[float]]:
        vectors: list[Sequence[float]] = []
        for text in texts:
            vec = [0.0] * self._DIM
            for ch in text.lower():
                vec[ord(ch) % self._DIM] += 1.0
            vectors.append(vec)
        return vectors


@pytest.fixture
async def store(tmp_path: Path):
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


def test_map_node_type_known_and_default() -> None:
    assert map_node_type("symbol") == GlyphNodeType.FUNCTION
    assert map_node_type("file") == GlyphNodeType.FILE
    assert map_node_type("class") == GlyphNodeType.CLASS
    # unknown AXON node types fall back to the code default, never crash
    assert map_node_type("totally-unknown") == GlyphNodeType.FUNCTION


def test_map_edge_type_known_and_default() -> None:
    assert map_edge_type("calls") == GlyphEdgeType.CALLS
    assert map_edge_type("imports") == GlyphEdgeType.IMPORTS
    # decision/provenance edges have no code analogue -> REFERENCES
    assert map_edge_type("touches") == GlyphEdgeType.REFERENCES
    assert map_edge_type("supersedes") == GlyphEdgeType.REFERENCES
    assert map_edge_type("totally-unknown") == GlyphEdgeType.REFERENCES


def test_embedder_adapter_wraps_axon_engine() -> None:
    class Engine:
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[float(len(t))] for t in texts]

    adapter = GlyphEmbedderAdapter(Engine())
    # accepts any Sequence (tuple), forwards to the engine as a list
    assert adapter.embed(("a", "bb")) == [[1.0], [2.0]]


async def test_context_returns_axon_contextpack_in_score_order(store: SessionStore) -> None:
    # a tiny code graph: a controller calls a service which calls a repository
    for nid in ("CampaignController", "CampaignService", "CampaignRepository"):
        await store.add_node(nid, "symbol", label=nid)
    await store.add_edge(
        Edge(source_id="CampaignController", target_id="CampaignService", type="calls")
    )
    await store.add_edge(
        Edge(source_id="CampaignService", target_id="CampaignRepository", type="calls")
    )

    source = GraphContextSource(store, FakeEmbedder(), hops=2, anchors=2)
    pack = await source.context("CampaignService", token_budget=500)

    assert isinstance(pack, ContextPack)
    assert pack.mode == "graph"
    assert pack.segments  # non-empty
    # the anchored service must surface in the joined text
    assert "CampaignService" in pack.text


async def test_context_synthesizes_missing_edge_endpoints(store: SessionStore) -> None:
    # 'imports' edge whose endpoints were never add_node'd (file-path ids)
    await store.add_edge(Edge(source_id="a/b.py", target_id="a/c.py", type="imports"))

    source = GraphContextSource(store, FakeEmbedder(), hops=1, anchors=1)
    pack = await source.context("a/b.py", token_budget=500)

    # no KeyError on graph reconstruction; the synthesized node is retrievable
    assert "a/b.py" in pack.text


async def test_context_empty_graph_returns_empty_pack(store: SessionStore) -> None:
    source = GraphContextSource(store, FakeEmbedder())
    pack = await source.context("anything")

    assert isinstance(pack, ContextPack)
    assert pack.mode == "graph"
    assert pack.segments == ()
