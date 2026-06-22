# dec-117 — Keep GLYPH for the code graph; borrow Graphiti's bi-temporal model for decision supersession

- Status: accepted
- Date: 2026-06-20
- Relates to: dec-101 (storage stack: SQLite source of truth, Neo4j dropped),
  dec-104 (event-driven, no idle cost), dec-115 (soft supersession via ranking
  penalty), dec-116 (delegate graph-aware retrieval to GLYPH).

## Context

[Graphiti](https://github.com/getzep/graphiti) (Zep) is a mature, widely-used
temporal knowledge-graph library. The question raised was whether AXON should
adopt it in place of, or alongside, GLYPH (`glyph-kg`) as the graph layer.

A close reading shows GLYPH and Graphiti are **not the same kind of graph** and
do not solve the same problem:

| Axis | GLYPH | Graphiti |
| --- | --- | --- |
| Graph type | Structural (code via tree-sitter; documents via LLM) | Temporal / bi-temporal facts with validity windows |
| Strong domain | Code: `calls`/`imports`, deterministic, no API key | Real-world facts that change over time |
| Temporal model | None | Bi-temporal: facts are invalidated, not deleted |
| Ingestion | Deterministic `CodeExtractor` (no LLM) + LLM `DocumentExtractor` | Episodes, **one LLM call per ingest** (structured output) |
| Retrieval | Entity anchor + N-hop expansion vs vector baseline | Hybrid (semantic + BM25 + traversal), graph-distance rerank |
| Storage | NetworkX in-process (default), Neo4j optional | Neo4j (primary), FalkorDB, Neptune, Kuzu |
| Maturity | v0.1.0 (ours) | ~27k stars, multi-backend, production-proven |

AXON's graph has two distinct needs, and each library wins a different one:

1. **Code-structure graph** (`calls`/`imports`, what `graph_source.py` maps
   today). GLYPH is the right fit: code-native, **deterministic, no LLM cost**,
   rebuilds in-process from the SQLite source of truth (dec-101). Graphiti is
   not code-aware and would charge an LLM call per file to approximate this —
   worse on every axis for code.

2. **Temporal decision graph** (dec-115: demote a stale decision when a newer
   one revises it). This is exactly what Graphiti's bi-temporal fact
   invalidation does natively. AXON's current supersession detector
   (cosine ≥ 0.93 + EN/PT revision verb) is a partial hand-rolled
   reimplementation of that model.

## Decision

1. **Keep GLYPH as the code-graph retrieval layer.** dec-116 stands unchanged.
   GLYPH's determinism and zero-LLM code extraction are the correct fit for the
   structural graph and align with dec-101/dec-104.

2. **Do not adopt Graphiti wholesale.** Its primary backend is Neo4j, which
   dec-101 deliberately dropped, and its LLM-per-episode ingestion contradicts
   dec-104's event-driven, no-idle-cost capture and AXON's deterministic
   extraction. Adopting it would reverse two settled decisions to solve a
   problem AXON already addresses at lower cost.

3. **Borrow the bi-temporal concept for decisions, not the dependency.** Evolve
   dec-115 from a similarity-penalty heuristic toward an explicit bi-temporal
   model on the existing SQLite `Decision` records: add `valid_from` /
   `invalidated_at` (or equivalent) so supersession is a recorded state
   transition with provenance, and recall can answer "what holds now" vs "what
   held at time T" without reintroducing a graph database. This imports
   Graphiti's strongest idea — invalidate, don't delete; query across time —
   while preserving the lossless, SQLite-source-of-truth model.

This mirrors the dec-115 pattern (borrow EpochDB's demote-don't-delete concept,
reject the dependency): take the idea, keep the architecture.

## Consequences

- No new runtime dependency and no change to dec-116. GLYPH remains the code
  graph layer.
- dec-115 supersession remains opt-in and default-off; the proposed bi-temporal
  fields are a forward design direction, to be specified and validated in a
  follow-up before any schema change. No migration is implied by this record.
- Graphiti is retained as a **design reference** for the temporal layer and as a
  benchmark target should AXON ever publish recall numbers, not as code AXON
  ships.

## Open follow-ups

- Specify the bi-temporal `Decision` fields and the recall query surface
  ("as-of" queries), with a regression test, before implementation (per the
  repo's test-first rule).
- Decide whether the temporal transition is written at capture time (when a
  revision verb / near-duplicate is confirmed) or remains read-time only as in
  dec-115.
