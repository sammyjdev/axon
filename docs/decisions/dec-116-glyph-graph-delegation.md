# dec-116 — Delegate graph-aware context retrieval to GLYPH

- Status: accepted
- Date: 2026-06-11
- Relates to: ADR-004 (split graph backends), dec-101 (storage stack: SQLite is
  the graph source of truth).

## Context

GLYPH (`sammyjdev/glyph-kg`, public) became the canonical knowledge-graph
retrieval library. AXON had been growing its own graph-context retrieval on top
of the consolidated SQLite code graph (nodes/edges from `index_file` /
`index_edges`, queried via `SessionStore.query_subgraph` and the Redis
dependency cache). Maintaining a second graph-retrieval implementation
duplicates effort and diverges from the now-canonical GLYPH semantics.

> Note on numbering: the originating task referred to "ADR-102/103". This
> repository tracks decisions as `dec-NNN`; the architecturally relevant graph
> decision is ADR-004 + dec-101, so the delegation is recorded here as dec-116
> and cross-linked from ADR-004 rather than from the unrelated dec-102 (router
> profiles) / dec-103 (cross-agent MCP transport).

## Decision

- AXON's graph-aware context source is **implemented by the GLYPH library**.
  `axon.context.graph_source.GraphContextSource` reads the consolidated SQLite
  graph, maps AXON nodes/edges to GLYPH `Node`/`Edge`, builds a `NetworkXStore`
  + `GraphRetriever` directly through their constructors (no temp file), and
  adapts the returned GLYPH `ContextPack` back to AXON's own `ContextPack`.
- The MCP layer's external contract is preserved: this is an implementation
  swap. Existing graph tools (`get_graph_neighbors`, `get_graph_path`,
  `search_code`) are unchanged; a new read-only `get_graph_context(query,
  token_budget)` tool exposes the GLYPH-backed path.
- Embedder: AXON reuses its existing `EmbedderEngine`, wrapped by
  `GlyphEmbedderAdapter` to satisfy GLYPH's `Embedder` protocol. GLYPH's
  optional `sentence-transformers` extra is intentionally not pulled.
- Dependency: `glyph-kg[retrieval]` pinned to a fixed SHA on GLYPH `main` (the
  merged completion of GLYPH P3–P6), keeping AXON's build reproducible. AXON
  delegates through the `GraphRetriever`/`NetworkXStore` constructors rather than
  the file-based facade, so the seam is stable across the pinned range.

### Type mapping (AXON → GLYPH)

| AXON | GLYPH |
| --- | --- |
| node `symbol` | `NodeType.FUNCTION` (code default) |
| node `file`/`module`/`class`/`function` | `NodeType.FILE/MODULE/CLASS/FUNCTION` |
| edge `calls` / `imports` | `EdgeType.CALLS` / `IMPORTS` |
| edge `inherits` / `defines` | `EdgeType.INHERITS` / `DEFINES` |
| edge `touches`/`supersedes`/`discussed_in`/`committed_as` | `EdgeType.REFERENCES` |

Edge endpoints with no persisted node row (file-path `imports` targets,
decision ids on `touches` edges) are synthesized: path-like ids → `FILE`, else
the code default. This prevents NetworkX from auto-creating typeless nodes.

## Rationale

- One canonical graph-retrieval implementation instead of two.
- Smallest coherent change: the SQLite source of truth (dec-101) and the
  external MCP contract are untouched; only the retrieval internals move to
  GLYPH, behind an adapter that owns the type mapping and the `ContextPack`
  shape conversion.
