"""GLYPH-backed graph-aware context source (ADR-102/103 delegation).

GLYPH (``glyph-kg``) is the canonical knowledge-graph retrieval library. AXON no
longer reimplements graph retrieval: this module reads the consolidated SQLite
code graph (ADR-103), maps AXON's nodes/edges onto GLYPH's :class:`Node` /
:class:`Edge`, builds an in-memory ``NetworkXStore`` + ``GraphRetriever`` straight
through the constructors (no temp JSON file), and adapts the GLYPH
``ContextPack`` back to AXON's own :class:`~axon.context.contracts.ContextPack`
so the MCP layer's external contract is unchanged.

Type mapping (declared, AXON -> GLYPH)
--------------------------------------
Nodes: AXON's code graph is symbol-centric (``add_node(..., "symbol")``), so
``symbol`` maps to ``NodeType.FUNCTION``. ``file``/``module``/``class``/
``function`` map verbatim; anything else falls back to ``FUNCTION`` (the code
default) rather than raising. Edge endpoints that were never ``add_node``'d
(e.g. file-path targets of ``imports`` edges, or decision ids on ``touches``
edges) are synthesized: path-like ids become ``FILE``, the rest ``FUNCTION``.

Edges: ``calls`` -> ``CALLS``, ``imports`` -> ``IMPORTS`` are AST facts.
``inherits``/``defines`` map verbatim if ever produced. Provenance/decision
edges (``touches``, ``supersedes``, ``discussed_in``, ``committed_as``) have no
code analogue, so they collapse to ``REFERENCES``; unknown types do too.
"""

from __future__ import annotations

from collections.abc import Sequence

from glyph.model.edge import Edge as GlyphEdge
from glyph.model.edge import EdgeType as GlyphEdgeType
from glyph.model.node import Node as GlyphNode
from glyph.model.node import NodeType as GlyphNodeType
from glyph.retrieval.graph import GraphRetriever
from glyph.store.networkx_store import NetworkXStore

from axon.context.contracts import ContextPack, RetrievalStrategy
from axon.store.session_store import SessionStore

_NODE_TYPE_MAP: dict[str, GlyphNodeType] = {
    "symbol": GlyphNodeType.FUNCTION,
    "file": GlyphNodeType.FILE,
    "module": GlyphNodeType.MODULE,
    "class": GlyphNodeType.CLASS,
    "function": GlyphNodeType.FUNCTION,
}
_DEFAULT_NODE_TYPE = GlyphNodeType.FUNCTION

_EDGE_TYPE_MAP: dict[str, GlyphEdgeType] = {
    "calls": GlyphEdgeType.CALLS,
    "imports": GlyphEdgeType.IMPORTS,
    "inherits": GlyphEdgeType.INHERITS,
    "defines": GlyphEdgeType.DEFINES,
    "touches": GlyphEdgeType.REFERENCES,
    "supersedes": GlyphEdgeType.REFERENCES,
    "discussed_in": GlyphEdgeType.REFERENCES,
    "committed_as": GlyphEdgeType.REFERENCES,
}
_DEFAULT_EDGE_TYPE = GlyphEdgeType.REFERENCES

# The delegation has no AXON-side strategy tuning; this is a stable descriptor so
# the adapted ContextPack stays well-formed for the MCP layer.
_GRAPH_STRATEGY = RetrievalStrategy(
    name="glyph-graph",
    contexts=(),
    max_segments=64,
    max_chars=16_000,
    prefer_local=True,
    enable_compression=False,
)


def map_node_type(axon_type: str) -> GlyphNodeType:
    """Map an AXON node ``type`` string to a GLYPH :class:`NodeType`."""
    return _NODE_TYPE_MAP.get((axon_type or "").lower(), _DEFAULT_NODE_TYPE)


def map_edge_type(axon_type: str) -> GlyphEdgeType:
    """Map an AXON edge ``type`` string to a GLYPH :class:`EdgeType`."""
    return _EDGE_TYPE_MAP.get((axon_type or "").lower(), _DEFAULT_EDGE_TYPE)


def _synthesized_node_type(node_id: str) -> GlyphNodeType:
    """Best-effort type for an edge endpoint that has no persisted node row."""
    if "/" in node_id or "\\" in node_id or node_id.endswith((".py", ".ts", ".tsx", ".java")):
        return GlyphNodeType.FILE
    return _DEFAULT_NODE_TYPE


class GlyphEmbedderAdapter:
    """Wrap AXON's :class:`EmbedderEngine` to satisfy ``glyph.embed.port.Embedder``.

    The protocol wants ``embed(texts: Sequence[str]) -> list[Vector]``; AXON's
    engine type-hints ``list[str]``, so we normalize the input to a list and
    forward. We reuse AXON's existing embedder rather than pulling GLYPH's
    optional ``sentence-transformers`` extra.
    """

    def __init__(self, engine: object) -> None:
        self._engine = engine

    def embed(self, texts: Sequence[str]) -> list[Sequence[float]]:
        return self._engine.embed(list(texts))  # type: ignore[attr-defined, no-any-return]


def _ensure_embedder(embedder: object) -> object:
    """Pass through anything that already satisfies the Embedder protocol; else wrap."""
    embed = getattr(embedder, "embed", None)
    if callable(embed):
        return embedder
    raise TypeError("embedder must expose a callable .embed(texts)")


class GraphContextSource:
    """AXON's graph-aware context source, delegated to the GLYPH library.

    Construct with an open :class:`SessionStore` (the ADR-103 SQLite graph) and
    an embedder (AXON's :class:`EmbedderEngine`, or any object exposing
    ``embed(texts) -> list[Sequence[float]]``). ``context`` returns an AXON
    :class:`ContextPack`, preserving the contract the MCP layer consumes today.
    """

    def __init__(
        self,
        store: SessionStore,
        embedder: object,
        *,
        hops: int = 2,
        anchors: int = 3,
    ) -> None:
        self._store = store
        self._embedder = _ensure_embedder(embedder)
        self._hops = hops
        self._anchors = anchors

    async def _build_glyph_graph(self) -> tuple[NetworkXStore, list[GlyphNode]]:
        """Read the consolidated SQLite graph and materialize a GLYPH graph."""
        node_rows = await self._store.all_nodes()
        edges = await self._store.all_edges()

        nodes_by_id: dict[str, GlyphNode] = {}
        for row in node_rows:
            node_id = str(row["id"])
            label = str(row.get("label") or node_id)
            nodes_by_id[node_id] = GlyphNode(
                id=node_id, type=map_node_type(str(row["type"])), label=label
            )

        glyph_edges: list[GlyphEdge] = []
        for edge in edges:
            for endpoint in (edge.source_id, edge.target_id):
                # NetworkX would auto-create a bare node for an unknown endpoint,
                # which then has no type/label and breaks subgraph reconstruction.
                if endpoint not in nodes_by_id:
                    nodes_by_id[endpoint] = GlyphNode(
                        id=endpoint, type=_synthesized_node_type(endpoint), label=endpoint
                    )
            glyph_edges.append(
                GlyphEdge(
                    src=edge.source_id,
                    dst=edge.target_id,
                    type=map_edge_type(edge.type),
                )
            )

        store = NetworkXStore()
        nodes = list(nodes_by_id.values())
        store.upsert_nodes(nodes)
        store.upsert_edges(glyph_edges)
        return store, nodes

    async def context(self, query: str, token_budget: int = 1000) -> ContextPack:
        """Delegate graph-aware retrieval to GLYPH and adapt the result."""
        store, nodes = await self._build_glyph_graph()
        if not nodes:
            return self._adapt_segments((), token_estimate=0)

        retriever = GraphRetriever(
            store, self._embedder, nodes, hops=self._hops, anchors=self._anchors
        )
        glyph_pack = retriever.retrieve(query, token_budget)
        return self._adapt_pack(glyph_pack)

    def _adapt_pack(self, glyph_pack: object) -> ContextPack:
        # GLYPH already returns segments in score order (desc), tie-broken by
        # source; join their text in that order.
        segments = tuple(seg.text for seg in glyph_pack.segments)  # type: ignore[attr-defined]
        provenance = tuple(
            (str(seg.source), f"{seg.score:.4f}")
            for seg in glyph_pack.segments  # type: ignore[attr-defined]
        )
        return self._adapt_segments(
            segments,
            token_estimate=int(glyph_pack.token_estimate),  # type: ignore[attr-defined]
            provenance=provenance,
            mode=str(glyph_pack.mode),  # type: ignore[attr-defined]
        )

    def _adapt_segments(
        self,
        segments: tuple[str, ...],
        *,
        token_estimate: int,
        provenance: tuple[tuple[str, str], ...] = (),
        mode: str = "graph",
    ) -> ContextPack:
        metadata = (
            ("backend", "glyph"),
            ("token_estimate", str(token_estimate)),
            *provenance,
        )
        return ContextPack(
            strategy=_GRAPH_STRATEGY,
            task_type="CODE_ANALYSIS",
            profile=None,
            mode=mode,
            contexts=(),
            segments=segments,
            metadata=metadata,
        )
