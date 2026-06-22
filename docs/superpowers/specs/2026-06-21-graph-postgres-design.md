# Design: graph (nodes/edges) -> Postgres (dec-121 step 3, wave 2)

- Date: 2026-06-21
- Status: approved (brainstorming), pending spec review
- Implements: dec-121 step 3 (relational source of truth SQLite -> Postgres),
  WAVE 2 of 4 (graph nodes/edges)
- Builds on: file_index wave (wave 1, merged); pgvector store + cutover (steps 1-2)
- Relates to: dec-116 / dec-117 (GLYPH owns graph retrieval)
- Branch: `feat/graph-postgres` (off `feat/rtkx-consume`)

## Goal

Move the code graph (nodes/edges) from SQLite to PostgreSQL behind a new
`GraphRepository` Protocol, selectable by `AXON_GRAPH_BACKEND`, with the existing
data copied over. `SessionStore` keeps its 7 graph-method signatures but
delegates them to the configured repository, so all ~9 consumer call sites and
GLYPH's `graph_source.py` are unchanged. Decisions and session continuity stay
on SQLite (their own waves). GLYPH retrieval is untouched (ADR Option A).

## Scope

In scope (this wave):

- A `GraphRepository` Protocol with the 7 graph methods.
- `SqliteGraphRepository` (the current SQL, extracted) and `PostgresGraphRepository`
  (asyncpg, Python BFS preserved).
- `SessionStore` delegating its 7 graph methods to the configured repository.
- `RuntimeConfig.graph_backend` (env > axon.toml > default sqlite).
- A one-shot data-copy script (SQLite nodes/edges -> Postgres).
- The cutover (copy + flip) + a runbook note.

Out of scope (later waves):

- Decisions/ADRs (wave 3), session continuity (wave 4).
- GLYPH internals; recursive-CTE `PostgresGraphStore` (ADR Option B) - we keep
  the in-memory NetworkXStore fed via `all_nodes`/`all_edges` (Option A).
- JSONB payloads - payload stays a JSON text column for byte-for-byte parity with
  the SQLite repository this wave (JSONB is a later optimization).

## Decisions (from brainstorming)

1. **Seam: SessionStore delegates to a `GraphRepository`.** The 7 graph methods
   are called on a `SessionStore` instance by ~9 sites; extracting a Protocol and
   delegating keeps every call site and GLYPH unchanged, while letting the graph
   backend be selected independently of decisions/sessions (which stay SQLite).
2. **Per-concern flag `AXON_GRAPH_BACKEND`** (env > axon.toml `[runtime]
   graph_backend` > default sqlite), flipped after validation; consolidates into
   `AXON_DB_BACKEND` at the end of step 3.
3. **Copy the data, do not rebuild.** Nodes are index-derived (rebuildable) but
   the `touches` edges are derived from git events (commit history) and are NOT
   reproduced by a pure re-index. A one-shot copy (tiny: ~3400 nodes, ~376 edges)
   preserves everything.
4. **GLYPH unchanged (ADR Option A).** `graph_source.py` builds a fresh in-memory
   `NetworkXStore` from `all_nodes`/`all_edges` per query; these delegate to the
   configured repository, so GLYPH works on either backend with no change.

## The GraphRepository contract (what both repositories satisfy)

Extracted from `SessionStore`'s current graph methods (the SQLite behavior is the
reference):

- `add_node(node_id, node_type, *, label="", payload=None)` - upsert by id
  (`ON CONFLICT(id) DO UPDATE` of type/label/payload/updated_at).
- `add_edge(edge: Edge)` - insert-if-absent (SQLite `INSERT OR IGNORE` ->
  Postgres `ON CONFLICT (source_id, target_id, type) DO NOTHING`).
- `get_node(node_id) -> dict | None` - id/type/label/payload(parsed)/created_at/
  updated_at.
- `query_subgraph(node_id, depth=2) -> {root, nodes(sorted), edges[{source,target,type}]}`
  - bounded BFS expanding `source_id IN frontier`.
- `shortest_path(from_node, to_node, max_depth=10) -> list[str] | None` - BFS,
  returns the inclusive id path or None.
- `all_nodes() -> list[dict]` - full node scan ordered by id (GLYPH feed).
- `all_edges() -> list[Edge]` - full edge scan ordered by (source,target,type).

`Edge` is the existing model (`source_id, target_id, type, payload`). Payload is
JSON: stored as a text column, `json.dumps` on write, `json.loads` on read, on
BOTH backends, so round-trips are identical.

## Components

### GraphRepository Protocol + SqliteGraphRepository (`src/axon/store/graph_repository.py`)

- `GraphRepository(Protocol)` declares the 7 methods above.
- `SqliteGraphRepository(conn, lock)` - the CURRENT SessionStore graph SQL moved
  verbatim, sharing SessionStore's aiosqlite connection and `asyncio.Lock` (like
  `SqliteFileCache`). Behavior unchanged.

### PostgresGraphRepository (`src/axon/store/pg_graph_repository.py`)

- `__init__(dsn)` - lazy asyncpg pool (mirrors `PgVectorStore`/`PostgresFileCache`).
- `ensure_schema()` - idempotent; ports migrations 001 + 002:
  - `nodes (id text PRIMARY KEY, type text NOT NULL, label text DEFAULT '',
    payload text, created_at text NOT NULL, updated_at text NOT NULL)`
  - `edges (source_id text NOT NULL, target_id text NOT NULL, type text NOT NULL,
    payload text, created_at text NOT NULL, UNIQUE (source_id, target_id, type))`
  - indexes on `edges(source_id)` for the BFS expansions.
- The 7 methods mirror SqliteGraphRepository: `?` -> `$1..`; `INSERT OR IGNORE`
  -> `ON CONFLICT (...) DO NOTHING`; the BFS `source_id IN (dynamic placeholders)`
  -> `source_id = ANY($1::text[])` (asyncpg array bind). Returned shapes
  (dicts/`Edge`) identical. `close()` closes the pool.

### SessionStore delegation (`src/axon/store/session_store.py`)

- `SessionStore` resolves its graph backend internally (so the 13 consumers that
  do `SessionStore(db_path)` are unchanged): read the resolved
  `graph_backend` (via the config resolver / env). When `sqlite`, lazily build a
  `SqliteGraphRepository(self._connection(), self._lock)`; when `postgres`, a
  `PostgresGraphRepository(pg_url)` (+ `ensure_schema`).
- The 7 graph methods become thin delegations: e.g.
  `async def add_node(self, *a, **k): return await self._graph().add_node(*a, **k)`.
- `close()` also closes the graph repository if it owns a Postgres pool.
- Decisions/ADRs/sessions/memories/notes/code_changes stay on the aiosqlite
  connection exactly as now.

### Config (`RuntimeConfig.graph_backend`)

- New defaulted trailing field `graph_backend: str = "sqlite"` (same pattern as
  `vector_backend` / `fileindex_backend`).
- `_resolve_graph_backend(overrides)`: `AXON_GRAPH_BACKEND` env > toml
  `graph_backend` > `sqlite`; validated `{sqlite, postgres}`; `"graph_backend"`
  added to the toml allowlist. Default flips to `postgres` only in the cutover task.

### Data-copy script (`scripts/migrate_graph.py`)

- Reads `all_nodes()` / `all_edges()` from the SQLite repository and writes them
  via `add_node` / `add_edge` to the Postgres repository (idempotent - re-runnable).
  Prints copied counts. No model load. Manual cutover aid.

## GLYPH (unchanged)

`graph_source.py` calls `self._store.all_nodes()` / `all_edges()` and builds a
fresh `NetworkXStore` per query. Since those delegate to the configured
repository, GLYPH retrieval is identical whether the graph lives in SQLite or
Postgres. No GLYPH code changes (dec-116/117 stand).

## Data flow

Indexing (`code/indexer.py`, `code/resolver.py`, `hooks/git_event.py`,
`obsidian/importer.py`) calls `store.add_node` / `store.add_edge` -> delegated to
the configured repository. Query (`pb graph`, `mcp` graph tools) calls
`store.query_subgraph` / `shortest_path` -> delegated. GLYPH context builds from
`all_nodes`/`all_edges` -> delegated. The backend is chosen once in SessionStore.

## Error handling

- Unknown `graph_backend`: `ValueError` at config load.
- postgres selected but unreachable: asyncpg pool surfaces the error on first use
  (no silent fallback to SQLite).
- `ensure_schema` idempotent (`IF NOT EXISTS`).
- The `add_edge` idempotency (`DO NOTHING` on the unique key) matches the SQLite
  `INSERT OR IGNORE`, so a re-copy never duplicates edges.

## Cutover (data copy, then flip)

1. Bring up `axon-postgres`.
2. `python scripts/migrate_graph.py` - copy nodes/edges SQLite -> Postgres.
3. Validate: Postgres `nodes`/`edges` counts match SQLite (parity); a GLYPH-backed
   query (`pb graph subgraph <id>` or a recall/context call) returns the same
   neighborhood under `AXON_GRAPH_BACKEND=postgres`.
4. Flip `graph_backend = "postgres"` in `axon.toml`.
5. Rollback: set `sqlite`; the SQLite graph is untouched (the copy is one-way and
   non-destructive).

## Testing strategy

1. PostgresGraphRepository conformance (testcontainers[postgres], mirrors
   SqliteGraphRepository): add_node upsert; add_edge idempotent (no dup on the
   unique key); get_node; query_subgraph BFS depth bound + edge/node shape;
   shortest_path (path found, no path, same-node); all_nodes/all_edges ordering;
   payload JSON round-trip; ensure_schema idempotent.
2. SessionStore delegation: with `graph_backend=postgres`, `store.add_node` etc.
   route to the Postgres repository; with `sqlite`, to the SQLite one; decisions/
   sessions still work (unaffected).
3. Config precedence (no live backend): default sqlite, env override, toml
   override, unknown raises.
4. Copy script: count parity after copy; re-run is idempotent.

## Success criteria

1. `PostgresGraphRepository` passes conformance and behaves identically to
   `SqliteGraphRepository` (upsert, idempotent edges, BFS, ordering, payload).
2. `SessionStore` delegates the 7 graph methods by `graph_backend`; the ~9
   consumer call sites and GLYPH are unchanged; decisions/sessions stay SQLite.
3. The copy script moves nodes/edges with count parity and is idempotent; a
   GLYPH query returns the same result on Postgres as on SQLite.
4. SQLite remains default and fully working until the flip; rollback is a
   one-line config change with the SQLite graph intact.
