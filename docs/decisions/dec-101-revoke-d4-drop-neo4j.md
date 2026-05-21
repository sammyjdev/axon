# dec-101 — Revoke D4 (multi-backend graph): drop Neo4j

- Status: accepted
- Date: 2026-05-21
- Revokes (partial): ADR-004 "Split graph backends by responsibility"

## Context

ADR-004 (D4) split graph storage: Redis for code dependency relationships,
Neo4j reserved for Mem0-style memory relationships. In practice Neo4j was only
partially wired (Cypher builders in `store/graph_namespace.py`, a read path in
the MCP server) and never populated. mem0 runs locally with a Qdrant vector
backend and does not require a Neo4j graph store.

## Decision

- Remove Neo4j from the architecture.
- Keep Redis as the code-dependency graph cache.
- Keep Qdrant as the code vector store (powers `search_code`) and as the mem0
  vector backend.
- Add mem0 as the semantic memory layer, configured local-only over Qdrant.

Resulting storage model: SQLite (source of truth) + Redis (graph cache) +
Qdrant (vector / code search) + mem0 (semantic memory).

## Rationale

- Neo4j added an operational dependency with no realized value.
- Qdrant is the working vector store; removing it would be a regression in code
  search, not a simplification. The original AXON draft proposed dropping
  Qdrant — that was a factual error and is corrected here.
- One fewer service to run improves the local-first story.

## Consequences

- `[project.optional-dependencies] graph` (neo4j) becomes dead — prune in Phase 2.
- `store/graph_namespace.py` Cypher builders become dead code — flag, do not
  delete until Phase 2.
- `memory/config.py` drops its Neo4j block.
