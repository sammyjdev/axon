# dec-121 — Unify persistence on PostgreSQL (pgvector + JSONB); keep GLYPH as the graph retrieval layer

- Status: proposed
- Date: 2026-06-20
- Supersedes: dec-101 (storage stack: SQLite + Redis + Qdrant + mem0)
- Relates to: dec-116 / dec-117 (GLYPH owns graph retrieval), dec-119
  (file-backed canonical activity stores), dec-120 (lean deps)

## Context

dec-101 settled storage as four moving parts: SQLite (source of truth), Redis
(code-dependency subgraph cache), Qdrant (code vectors + mem0 backend), mem0
(semantic memory). That split now costs more than it returns:

- No cross-store transaction. One logical index operation writes vectors
  (Qdrant), edges/nodes (SQLite), and decisions (SQLite) with no atomic
  boundary. The crash-safety / orphan-reconcile machinery in the 2026-06 perf
  overhaul (pending sentinel, per-file delete-by-file, subtree-scoped D6
  cleanup) exists specifically to compensate for that missing transaction.
- Operational surface: two extra runtime services (Qdrant, Redis), two extra
  clients, two extra backup paths.
- Scale reality: the live index is ~10^2-10^4 points for a single self-hosted
  user. None of Qdrant's differentiators (quantization, distributed sharding)
  are exercised.

A first draft of this ADR over-reached and proposed putting the graph itself in
Postgres (Apache AGE / Cypher). Reading the GLYPH code corrected that:

GLYPH (`glyph/`) is a graph **retrieval library with pluggable storage**, not a
database. Its `GraphStore` is a `Protocol` (`glyph/store/port.py`) with five
operations: `upsert_nodes`, `upsert_edges`, `neighbors`, `subgraph`,
`shortest_path`. Two backends ship: `NetworkXStore` (in-memory `MultiDiGraph`)
and `Neo4jStore`. Retrieval (`glyph/retrieval/`) layers a graph retriever
(neighborhood expansion plus vector ranking of node labels via an in-memory
cosine index) and a `HybridRetriever` (reciprocal-rank fusion of graph + vector
arms) over whatever store is injected. GLYPH does not own persistence: per
dec-116, AXON reads its nodes/edges out of `SessionStore` (the dec-101 SQLite
graph), maps them to GLYPH `Node`/`Edge`, and builds a fresh in-memory
`NetworkXStore` per query (`axon/context/graph_source.py:167-171`).

So "GLYPH owns the graph" (dec-117) means GLYPH owns graph **retrieval**.
Persistence is a separate, swappable concern. Postgres and GLYPH are
complementary, not competing.

## Decision (proposed)

Consolidate **persistence** onto a single PostgreSQL instance; keep GLYPH as the
graph **retrieval** layer.

| Concern | Today | Proposed |
| --- | --- | --- |
| Relational source of truth (decisions, ADRs, file_index, sessions, commits, nodes, edges) | SQLite | native PostgreSQL tables |
| Vector search (bge-base 768d, cosine, top-k) | Qdrant | `pgvector` (HNSW index; `vector`/`halfvec`) |
| Document / metadata payloads | ad hoc | `JSONB` + GIN |
| Subgraph cache | Redis | indexed tables / materialized views (warm `shared_buffers`) |
| Graph retrieval (neighbors / subgraph / shortest_path / hybrid) | **GLYPH** (NetworkX, fed from SQLite) | **GLYPH, unchanged** (NetworkX, fed from Postgres) |

Retire Qdrant and Redis from the default runtime. Do not move graph retrieval
into Postgres; GLYPH keeps it (dec-116 / dec-117 stand).

### Graph: two ways to back GLYPH with Postgres

- **Option A (minimal, recommended at current scale).** Keep GLYPH's
  in-memory `NetworkXStore`. Change only where the nodes/edges come from:
  `SessionStore` reads them from Postgres instead of SQLite;
  `graph_source.py` still does `NetworkXStore(); upsert_nodes; upsert_edges`.
  GLYPH is untouched, and this matches GLYPH's "zero servers, in-memory, fits
  the target corpus" design. Effort: repoint `SessionStore`'s graph tables.

- **Option B (native, only if the graph outgrows memory).** Add a
  `PostgresGraphStore` implementing GLYPH's five-method `GraphStore` protocol
  directly over Postgres, so GLYPH never materializes the whole graph:
  - `upsert_nodes` / `upsert_edges` -> `INSERT ... ON CONFLICT`.
  - `neighbors` / `subgraph` -> a bounded `WITH RECURSIVE` CTE (N-hop BFS),
    mirroring `NetworkXStore`'s `single_source_shortest_path_length(cutoff=hops)`.
  - `shortest_path` -> recursive CTE (or keep the in-memory path for small
    graphs).
  Effort: one new module implementing five methods; GLYPH's retrievers and
  AXON's `graph_source` adapter are unchanged because they only depend on the
  protocol.

The node-label vector ranking GLYPH does for graph retrieval can stay its
in-memory cosine index, or be backed by the same `pgvector` table; either is
behind GLYPH's `Embedder`/index seam.

## Out of scope (stays as-is)

- **GLYPH graph retrieval** (dec-116 / dec-117): unchanged.
- **dec-119 activity / savings stores.** `TraceStore.records.jsonl` and the
  compression telemetry are append-only logs that `familiar.py`'s
  `ActivityPoller` tails by byte offset. That streaming-tail model does not map
  cleanly onto a relational table; keep these file-backed (or, if they ever move
  to Postgres, drive `familiar`/`dashboard` off `LISTEN/NOTIFY` instead of
  offset tailing). This ADR does not touch them.

## Rationale

- ACID across what were three stores: indexing a file becomes one transaction
  over (chunk vectors + nodes/edges + file_index + decisions). Entire classes of
  reconcile/orphan/crash-safety bugs stop being possible rather than being
  guarded against.
- Fewer moving parts: one connection pool, one backup, no Qdrant/Redis
  processes - directly serves dec-120's lean-deps goal.
- Adequate at scale: `pgvector` HNSW handles up to ~10^6 vectors comfortably;
  AXON is orders of magnitude below. Warm `shared_buffers` covers the
  low-latency subgraph reads Redis filled.
- GLYPH's pluggable-store design means none of this disturbs the accepted graph
  decision.

## Consequences and tradeoffs

- Gives up Qdrant-specific features (quantization, distributed scale) and Redis
  sub-ms in-memory cache / pub-sub. Neither is used at AXON's scale; either can
  be reintroduced narrowly later without touching the source of truth.
- `pgvector` exposes fewer recall knobs than a dedicated engine; HNSW params
  (`m`, `ef_construction`, `ef_search`) plus the regression-based recall guard
  (dec-115 / `tests/recall`) are the mitigation. The recall gate must stay green
  across the migration.
- Wrong call only at scales AXON does not have: billions of vectors,
  multi-tenant, or a hard sub-ms distributed-cache requirement.

## Migration outline (incremental, each step gated by the recall guard)

1. Stand up Postgres + `pgvector`; add a `pgvector`-backed `VectorStore` behind
   the existing interface (no caller changes).
2. Blue/green the vectors into Postgres; run `AXON_RUN_RECALL=1` and require no
   regression vs the committed baseline before cutover.
3. Move the relational source of truth (SQLite -> Postgres), including the
   nodes/edges tables; port `store/migrations`. Graph retrieval keeps using
   GLYPH via Option A (NetworkXStore fed from Postgres).
4. Replace the Redis subgraph cache with indexed tables / materialized views;
   drop Redis.
5. Remove Qdrant and Redis from runtime config and `docker-compose`; mark
   dec-101 superseded in `docs/ADR.md`.
6. Adopt Option B (`PostgresGraphStore`) only if graph size later exceeds the
   in-memory budget.

Status stays `proposed` until step 2's recall gate passes on real data.

## Update 2026-06-29 — execution started, sub-decisions resolved

The migration is further along than the outline assumed: the runtime loader
already defaults every relational concern to `postgres` and the vector backend to
`pgvector`, and PG implementations exist for all five concerns
(`Postgres{Decision,Graph,Session}Repository`, `PostgresFileCache`,
`PgVectorStore`). The remaining work is finishing the long tail and deleting the
SQLite/Qdrant/Redis paths. Three sub-decisions, resolved after investigating real
usage (these refine the migration outline above):

- **Decisions/ADRs backfill: DONE.** `pb migrate decisions-sqlite-to-pg`
  (`src/axon/store/decision_backfill.py`) shipped and was applied: 110 legacy
  decisions copied + 5 PG-native renumbered → 115 decisions, 33 ADRs. See
  commits 46ab541..823febb.
- **Historical session/commit data: START CLEAN (no backfill).** Refines outline
  step 3. `sessions`, `session_memory`, `session_note`, `code_change`, `commits`
  are NOT migrated; PG starts empty for them. `nodes/edges` and `file_index`
  regenerate via re-index. Accepts loss of pre-migration session history.
- **Mem0: DROP (not migrate).** Mem0 is orphaned — no MCP tool or recall path
  invokes it; only `pb memory smoke` + an `axon_health` presence probe. Remove
  the `memory/mem0_tool.py` + `memory/config.py` files, the `memory_app` CLI
  sub-app, and the `mem0ai` dependency. `recall/strategy.py`'s `semantic_search`
  seam is kept as the future pgvector-semantic-recall extension point.
- **Redis: PORT the live half, DELETE the dead half.** Corrects outline step 4
  ("replace subgraph cache with materialized views"): the `subgraph:*` cache +
  `traverse()` + `upsert_deps_batch()` are dead (zero production callers — the
  no-op `git_event.py` `invalidate()` included) and are deleted outright. The
  `dep:*` call-graph backs ONE live MCP tool, `get_dependencies`, and is ported
  to a small `symbol_deps` PG table.

### Phasing (each phase independently shippable; backend switch is the fallback until the final delete)

1. **Phase 1 — vector (Qdrant out).** pgvector-only factory/runtime, delete the
   Qdrant `VectorStore`, drop Mem0 + `mem0ai`; recall guard on real data is the
   acceptance gate that promotes this slice to `accepted`. Plan:
   `docs/superpowers/plans/2026-06-29-dec121-phase1-retire-qdrant.md`.
2. **Phase 2 — graph + Redis (Redis out).** Port `dep:*` → `symbol_deps` PG
   table; delete the dead `subgraph:*` cache; fix `graph_source.py`'s in-memory
   GLYPH cache invalidation (today keys on the SQLite WAL mtime — a no-op under
   PG). Graph retrieval stays GLYPH/NetworkX (Option A).
3. **Phase 3 — relational (SQLite out).** Fix the 6 callsites that bypass the
   repository abstraction (`familiar.py`, `__main__.py`, `validation/aggregate.py`,
   `expansion/service.py`, `pb.py adr sync`, the index `GraphStore` sites);
   port or drop `FailureStore`/`OutcomeStore`; delete the SQLite repositories,
   `SessionStore`'s `aiosqlite` connection, `store/migrations/*.sql`, and the
   `aiosqlite` dependency.

Status remains `proposed`; the vector slice flips to `accepted` when Phase 1's
recall gate passes on real data (Phase 1 plan Task 5).
