# Design: PgVectorStore - a pgvector backend behind the VectorStore interface

- Date: 2026-06-21
- Status: approved (brainstorming), pending spec review
- Implements: dec-121 (unify persistence on Postgres), step 1 only
- Branch: `feat/pgvector-store`

## Goal

Add a PostgreSQL/`pgvector` implementation of the existing `VectorStore`
interface, selectable at runtime, running in parallel to Qdrant. This is the
first and only step of dec-121 in this cycle: non-destructive, reversible, and
gated by the recall guard. It proves the Postgres direction before anything
touches the relational source of truth, Redis, or the graph.

## Scope

In scope (this cycle):

- A `PgVectorStore` class implementing the same surface as `VectorStore`.
- A `make_vector_store()` factory selecting the backend by env var.
- A `pgvector`-backed schema created idempotently.
- A `docker-compose` service for local dev; `testcontainers[postgres]` for tests.
- A pgvector path in the recall harness so the recall guard can validate it.

Out of scope (later dec-121 cycles, explicitly not touched here):

- SQLite -> Postgres for the relational source of truth (decisions, nodes/edges,
  file_index, sessions).
- Replacing the Redis subgraph cache.
- Graph retrieval / GLYPH (stays exactly as-is; dec-116/117).
- Removing Qdrant. Qdrant remains the default backend.

## Decisions (from brainstorming)

1. Scope: vector store only. (Smallest reversible step that proves the approach.)
2. Deployment: `docker-compose` (pgvector image) for dev + `testcontainers[postgres]`
   for tests, mirroring the existing `testcontainers[qdrant]` pattern.
3. Backend selection: env var `AXON_VECTOR_BACKEND=qdrant|pgvector` (default
   `qdrant`) read by a `make_vector_store()` factory. Flip by env, zero caller
   logic change, instantly reversible.
4. Schema: ONE `embeddings` table with a `ctx` column (filtered in `WHERE`),
   HNSW cosine index. Not per-ctx tables. `work` restricted-context isolation
   stays a query-level concern in this cycle (Row-Level Security is a deferred
   option, noted but not built now).

## Interface parity (the contract PgVectorStore must satisfy)

`src/axon/store/vector_store.py` defines `VectorStore` with:

- `__init__(url="http://localhost:6333")`
- `async ensure_collections()`
- `async upsert(chunk: Chunk)`
- `async upsert_batch(chunks: list[Chunk])`
- `async search(query_vector, collections, language=None, project=None, top_k=5, max_depth=1)`
- `async delete_by_file(ctx: str, file_path: str)`
- `async close()`

`Chunk` (the VectorChunk) fields: `id, vector, file_path, language, chunk_type,
symbol, project, ctx, content, git_commit`.

`PgVectorStore` implements the same async methods with identical signatures and
return shapes, so every caller works unchanged. Notes:

- `ensure_collections()` -> PgVectorStore exposes a method named exactly
  `ensure_collections()` (interface parity; callers are unchanged); internally it
  runs the idempotent schema setup (`CREATE EXTENSION` + table + indexes). The
  spec refers to that internal step as "ensure_schema" for readability, but the
  public method name is `ensure_collections`.
- `search(..., collections, ...)` -> `collections` is the list of ctx names;
  maps to `WHERE ctx = ANY($collections)` plus optional `language`/`project`
  filters. The returned hit shape (score + payload fields) must match what
  `search_code` / the recall harness already consume.
- `max_depth` -> confirm its current Qdrant semantics during planning. If it is
  graph expansion (not pure vector search), the pgvector path mirrors the same
  behavior or documents it as a pass-through; the plan resolves this before
  implementation.

## Components

### PgVectorStore (`src/axon/store/pg_vector_store.py`)

- Connects via `AXON_PG_URL`. Driver: prefer `asyncpg` (mature async Postgres
  driver, native `pgvector` support via `pgvector.asyncpg`); the plan confirms it
  is added to deps (the repo currently uses `aiosqlite` for SQLite, no Postgres
  driver yet) and falls back to `psycopg[binary,pool]` only if a concrete reason
  surfaces.
- `ensure_schema()`:
  - `CREATE EXTENSION IF NOT EXISTS vector;`
  - `CREATE TABLE IF NOT EXISTS embeddings (id uuid PRIMARY KEY, vector vector(768),
    ctx text NOT NULL, file_path text NOT NULL, language text, chunk_type text,
    symbol text, project text, content text, git_commit text DEFAULT '');`
  - `CREATE INDEX ... USING hnsw (vector vector_cosine_ops);`
  - `CREATE INDEX ... ON embeddings (ctx, file_path);`  (for delete_by_file)
- `upsert` / `upsert_batch`: `INSERT ... ON CONFLICT (id) DO UPDATE` so re-index
  is idempotent (matches Qdrant upsert semantics and the D1 stable chunk-id).
- `search`: `SELECT ..., 1 - (vector <=> $q) AS score FROM embeddings
  WHERE ctx = ANY($ctxs) [AND language=$l] [AND project=$p]
  ORDER BY vector <=> $q LIMIT $k`. Cosine distance operator `<=>`.
- `delete_by_file(ctx, file_path)`: `DELETE FROM embeddings WHERE ctx=$1 AND file_path=$2`.
- `close()`: close pool/connection.

### Factory (`make_vector_store()`)

- Reads `AXON_VECTOR_BACKEND` (default `qdrant`).
- Returns `VectorStore(url=runtime.qdrant_url)` or `PgVectorStore(dsn=runtime.pg_url)`.
- Replaces the ~10 direct `VectorStore(url=...)` call sites (pb.py x6,
  expansion/service.py, mcp/server.py, obsidian/importer.py). Each caller swaps
  `VectorStore(url=...)` for `make_vector_store()`; no other change.
- `AXON_PG_URL` added to `RuntimeConfig` (default a local docker DSN), alongside
  the existing `qdrant_url`.

### Local dev + tests

- `docker-compose`: a `postgres` service using `pgvector/pgvector:pg16` (or
  current), exposing the DSN `AXON_PG_URL` points at.
- Tests: `testcontainers[postgres]` spins a pgvector container; the
  parity/integration tests run against it. Unit-level logic (SQL building, DSN
  parsing) is tested without a container where possible.

### Recall harness pgvector path

`src/axon/benchmark/recall.py` currently drives `qdrant_client` directly against
a temp collection. Add a pgvector path so the gate can validate the new backend:

- `index_corpus` and `run_recall_guard` parameterize over the store backend (use
  `make_vector_store()` or a backend switch + a throwaway table/schema), so the
  same 20-query golden set runs against pgvector.
- The gate runs with `AXON_VECTOR_BACKEND=pgvector AXON_RUN_RECALL=1` and must
  show no per-query rank regression and no Top-3 drop vs the committed baseline
  (`tests/recall/baseline.json`). pgvector uses HNSW; the harness sets a sane
  `ef_search` so recall is comparable.

## Data flow

`index_path` -> `engine.embed(texts)` -> `make_vector_store()` (PgVectorStore
when `AXON_VECTOR_BACKEND=pgvector`) -> `upsert_batch` INSERTs rows (vector +
payload) in one transaction per batch -> `search` runs the cosine `ORDER BY
vector <=> q` with ctx/language/project filters. The D2/D4/D6 crash-safety and
reconcile logic in `index_path` is unchanged (it calls `delete_by_file` and
`upsert_batch` through the interface).

## Error handling

- Missing extension: `CREATE EXTENSION IF NOT EXISTS vector` fails loudly if the
  image lacks pgvector (surfaced as a clear startup error, not a silent skip).
- Idempotent schema: `IF NOT EXISTS` everywhere; re-running `ensure_schema` is a
  no-op.
- Connection failure: explicit error from the pool on first use; no silent
  fallback to Qdrant (the backend is an explicit choice).
- Dimension mismatch: the `vector(768)` column rejects wrong-dim inserts; the
  store asserts the embedding dimension at upsert.

## Testing strategy

1. Parity / integration (testcontainers[postgres] + pgvector):
   - `ensure_schema` idempotent (run twice, no error).
   - upsert -> search round-trip returns the inserted chunk at rank 1 for its own
     vector.
   - ctx filter: a `knowledge` query never returns `work` rows.
   - top-k ordering: results ordered by cosine similarity, `LIMIT k` respected.
   - `delete_by_file` removes exactly that file's rows (orphan-reconcile parity).
   - re-upsert same id updates in place (no duplication) - mirrors the perf
     overhaul's no-accumulation invariant.
2. Factory: `AXON_VECTOR_BACKEND` selects the right class; default is qdrant.
3. Recall gate: `AXON_VECTOR_BACKEND=pgvector AXON_RUN_RECALL=1` -> no regression
   vs baseline. This is the release gate for the cycle.

## Success criteria

1. `PgVectorStore` passes the parity/integration suite under testcontainers.
2. `AXON_VECTOR_BACKEND=pgvector` indexes and searches end-to-end via the CLI.
3. The recall guard is green against pgvector (no per-query regression, no Top-3
   drop vs the Qdrant baseline).
4. Qdrant remains the default and fully working (zero behavior change when
   `AXON_VECTOR_BACKEND` is unset).
