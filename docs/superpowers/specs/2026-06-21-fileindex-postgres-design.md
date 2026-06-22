# Design: file_index -> Postgres (dec-121 step 3, wave 1)

- Date: 2026-06-21
- Status: approved (brainstorming), pending spec review
- Implements: dec-121 (unify persistence on Postgres), step 3 (relational source
  of truth SQLite -> Postgres), WAVE 1 of 4 (file_index)
- Builds on: the pgvector store + cutover (steps 1-2, merged)
- Branch: `feat/fileindex-postgres` (off `feat/rtkx-consume`)

## Goal

Move the `file_index` (the incremental indexing cache) from SQLite to
PostgreSQL, behind the existing `FileCache` Protocol, selectable by a per-concern
config flag. This is the first and lowest-risk wave of the relational migration:
`file_index` is a CACHE (no data to migrate), it already has a Protocol, and its
acquisition point (`_open_file_cache`) owns a dedicated connection, so the rest
of `SessionStore` (graph, decisions, sessions) stays on SQLite untouched.

## Scope

In scope (this wave):

- A `PostgresFileCache` implementing the 4-method `FileCache` Protocol over
  asyncpg, with its own pool and idempotent schema.
- A `RuntimeConfig.fileindex_backend` setting (env > axon.toml > default sqlite).
- A backend selector in `_open_file_cache()`.
- The cutover (flip the default to postgres) + a runbook note.

Out of scope (later waves / steps):

- Graph nodes/edges (wave 2), decisions/ADRs (wave 3), session continuity
  (wave 4). They stay on SQLite this wave.
- `SessionStore`'s own aiosqlite connection and its ~26 other methods.
- Consolidating the per-concern flags into one `AXON_DB_BACKEND` (end of step 3).

## Decisions (from brainstorming)

1. **Waves, file_index first.** file_index is a cache (no data migration), is the
   highest-churn table, and already has a `FileCache` Protocol - the smallest,
   safest first wave.
2. **Per-concern backend flags.** `AXON_FILEINDEX_BACKEND` this wave; later waves
   add `AXON_GRAPH_BACKEND`, etc. Each defaults to `sqlite`, flips per wave after
   validation, and they consolidate into a single `AXON_DB_BACKEND` once step 3
   completes. This preserves per-wave independence and rollback.
3. **Mixed-backend during the wave is intentional.** file_index runs on Postgres
   while graph/decisions/sessions stay on SQLite. This is safe because
   `_open_file_cache` owns a dedicated connection separate from the
   graph/decisions `SessionStore`.

## The FileCache contract (what PostgresFileCache must satisfy)

`src/axon/store/file_cache.py` defines `FileCache` as a Protocol with four async
methods (the production `SqliteFileCache` is the reference behavior):

- `get_all_sha1s(ctx) -> dict[str, str]` - `{file_path_posix: sha1}` for
  `status='done'` rows only. `pending` rows are EXCLUDED (crash sentinels are
  treated as hash misses).
- `set_entry(file_path, ctx, sha1, chunk_count, *, status='done')` - upsert one
  row; `INSERT ... ON CONFLICT (file_path, ctx) DO UPDATE`. file_path normalized
  to posix.
- `delete_entry(file_path, ctx)` - remove one row (file removed from repo).
- `list_entries(ctx) -> list[tuple[file_path_posix, sha1]]` - ALL rows in ctx
  (any status), for orphan detection.

The `status` sentinel semantics (`pending` written before vector mutation,
`done` after flush) are load-bearing for crash-safety and must be preserved
byte-for-byte. Path normalization (`Path(file_path).as_posix()`) must match so
Windows and posix keys collide identically.

## Components

### PostgresFileCache (`src/axon/store/pg_file_cache.py`)

- `__init__(self, dsn: str)` - stores the DSN; lazily creates an asyncpg pool
  (mirrors `PgVectorStore`'s `_ensure_pool` pattern). No shared pool with
  PgVectorStore - a dedicated pool keeps the concern isolated and the wave
  self-contained.
- `ensure_schema()` - idempotent:
  - `CREATE TABLE IF NOT EXISTS file_index (file_path text NOT NULL, ctx text
    NOT NULL, sha1 text NOT NULL, status text NOT NULL DEFAULT 'done',
    chunk_count integer NOT NULL DEFAULT 0, indexed_at text NOT NULL,
    PRIMARY KEY (file_path, ctx));`
  - `CREATE INDEX IF NOT EXISTS ix_file_index_ctx ON file_index (ctx);`
  - `CREATE INDEX IF NOT EXISTS ix_file_index_status ON file_index (status);`
  - `indexed_at` stays `text` (ISO string) to match the SQLite row shape exactly
    and avoid any timestamp-format divergence in the cache.
- The 4 methods: SQLite `?` placeholders become asyncpg `$1..`; everything else
  (posix normalization, `status='done'` filter, ON CONFLICT upsert, fetch shapes)
  is identical to `SqliteFileCache`. No `asyncio.Lock` is needed - asyncpg pool
  connections are not shared concurrently the way the single aiosqlite connection
  is, and Postgres handles write concurrency.
- `close()` - close the pool.

### Backend selector (`_open_file_cache` in `src/axon/cli/pb.py`)

- Reads `_RUNTIME.fileindex_backend`. When `postgres`: build a
  `PostgresFileCache(dsn=_RUNTIME.pg_url)`, call `ensure_schema()`, and return
  `(cache, <its pool or a close-handle>)`. When `sqlite`: the current path
  (aiosqlite connect + `_apply_migrations` + `SqliteFileCache`).
- The return contract `(file_cache, handle)` is unchanged; callers do
  `file_cache, db_conn = await _open_file_cache()` and `await db_conn.close()`.
  asyncpg `Pool.close()` and aiosqlite `Connection.close()` are both
  `await`-able, so the 5 callers are untouched.

### Config (`RuntimeConfig.fileindex_backend`)

- New defaulted trailing field `fileindex_backend: str = "sqlite"` (same pattern
  as `vector_backend`, so existing manual `RuntimeConfig(...)` constructions are
  not broken).
- `load_runtime_config()` populates it via a `_resolve_fileindex_backend(overrides)`
  helper: `AXON_FILEINDEX_BACKEND` env > `axon.toml [runtime] fileindex_backend`
  > `sqlite`; validated to `{sqlite, postgres}`, else `ValueError`.
- `vector_backend` shipped its default already flipped to pgvector;
  `fileindex_backend` ships defaulting to `sqlite` and flips to `postgres` only
  in the final cutover task of this wave's plan.

## Data flow

`pb index` -> `_open_file_cache()` selects the cache by
`runtime.fileindex_backend` -> `index_path` reads/writes the cache through the
`FileCache` Protocol (`get_all_sha1s` for dedup, `set_entry` pending->done for
crash-safety, `delete_entry`/`list_entries` for orphan reconcile). Nothing in
`index_path` branches on the backend; selection is entirely in `_open_file_cache`.

## Error handling

- Unknown `fileindex_backend`: explicit `ValueError` at config load.
- postgres selected but Postgres unreachable: the asyncpg pool surfaces the
  connection error on first use (no silent fallback to SQLite - the backend is an
  explicit choice).
- Schema race: `IF NOT EXISTS` everywhere; `ensure_schema` is a no-op on re-run.

## Cutover (no data migration - file_index is a cache)

1. Bring up `axon-postgres`.
2. Set `AXON_FILEINDEX_BACKEND=postgres` and run a FULL index (the Postgres
   file_index starts empty, so every file re-embeds once - same incremental-cache
   behavior as the step-2 vector cutover; documented in `docs/MIGRATION.md`).
3. Validate: `file_index` row count non-zero in Postgres; a second index dedups
   (skips unchanged files); the recall gate is unaffected (vectors are a separate
   concern, already on pgvector).
4. Flip `fileindex_backend = "postgres"` in `axon.toml`.
5. Rollback: set it back to `sqlite`; the SQLite `file_index` is untouched (it
   just goes stale, and the next sqlite index reconciles it).

## Testing strategy

1. PostgresFileCache conformance (testcontainers[postgres], mirrors
   SqliteFileCache):
   - `set_entry` then `get_all_sha1s` returns the row; a `pending` row is
     EXCLUDED from `get_all_sha1s` but present in `list_entries`.
   - `set_entry` twice on the same (file_path, ctx) updates in place (no dup).
   - `delete_entry` removes exactly that row.
   - posix normalization: a backslash path and its posix form collide.
   - `ensure_schema` idempotent (run twice, no error).
2. Config precedence (no live backend): default sqlite, env override, toml
   override, unknown raises.
3. Selector: `_open_file_cache` returns a PostgresFileCache when
   `fileindex_backend=postgres`, SqliteFileCache otherwise.

## Success criteria

1. `PostgresFileCache` passes the conformance suite and behaves identically to
   `SqliteFileCache` (including the `pending`/`done` sentinel and posix keys).
2. `pb index` with `AXON_FILEINDEX_BACKEND=postgres` indexes and dedups
   end-to-end against the Postgres file_index.
3. SQLite remains the default and fully working until the flip; rollback to
   sqlite is a one-line config change.
4. The graph/decisions/sessions concerns are untouched (still SQLite).
