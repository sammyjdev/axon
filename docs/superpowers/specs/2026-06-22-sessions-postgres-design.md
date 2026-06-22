# Design: session continuity -> Postgres + backend consolidation (dec-121 step 3, wave 4)

- Date: 2026-06-22
- Status: approved (brainstorming), pending spec review
- Implements: dec-121 step 3 (relational source of truth SQLite -> Postgres),
  WAVE 4 of 4 (session continuity) + the per-concern -> AXON_DB_BACKEND consolidation
- Builds on: file_index (w1) + graph (w2) + decisions/ADRs (w3), merged
- Branch: `feat/sessions-postgres` (off `feat/rtkx-consume`)

## Goal

Move the session-continuity tables (`session_memory`, `session_note`,
`code_change`, `sessions`) to Postgres behind a `SessionRepository` Protocol,
selectable by `AXON_SESSIONS_BACKEND`, with the existing data copied over. After
this wave `SessionStore` has NO direct SQL left - it is a pure facade delegating
to the graph/decisions/sessions repositories (file_index is already separate via
`_open_file_cache`). Finally, consolidate the four per-concern backend flags into
a single optional `AXON_DB_BACKEND` master switch, closing dec-121 step 3.

## Scope

In scope:

- A `SessionRepository` Protocol (9 methods) + `SqliteSessionRepository`
  (extracted) + `PostgresSessionRepository` (asyncpg, plain columns).
- `SessionStore` delegating its session-continuity methods; `drain_pending`'s
  `code_change` branch delegating to the sessions repository.
- `RuntimeConfig.sessions_backend` (env > axon.toml > default sqlite).
- A one-shot data-copy script.
- The cutover (copy + flip) + runbook note.
- The consolidation: an `AXON_DB_BACKEND` / `[runtime] db_backend` master switch
  that all four concern resolvers fall back to.

Out of scope:

- file_index acquisition (already separate via `_open_file_cache`).
- Removing the per-concern flags (they remain as overrides; `AXON_DB_BACKEND` is
  an additional convenience tier, not a replacement).

## Decisions (from brainstorming)

1. **Same seam as waves 2-3** (Protocol + SessionStore delegation), per-concern
   flag `AXON_SESSIONS_BACKEND`. Plain Postgres columns (these tables have no
   JSON queries, so no JSONB/GIN - simpler than decisions).
2. **Copy the data.** Memories/notes/code_changes/sessions are continuity data
   (not regenerable from the index); a one-shot idempotent copy moves them. The
   live volume is tiny (~2 rows now), but the copy is the correct mechanism.
3. **Consolidate at the end.** With all four concerns on Postgres, add a single
   `AXON_DB_BACKEND` master flag so an operator can flip everything at once,
   while keeping the per-concern flags as fine-grained overrides.

## The SessionRepository contract

Extracted from SessionStore (SQLite behavior is the reference):

- `save_session_memory(mem: SessionMemory) -> int` - insert, return new id.
- `get_session_memories(project, limit=3) -> list[SessionMemory]` - newest first.
- `save_note(note: SessionNote) -> int` - insert, return new id.
- `get_notes(project, limit=10) -> list[SessionNote]` - newest first.
- `save_code_change(change: CodeChange)` - upsert by (commit_hash, file_path)
  (SQLite `INSERT OR REPLACE`), with the SQLite-lock pending fallback.
- `save_code_change_inner(change: CodeChange)` - the no-fallback upsert, used by
  `SessionStore.drain_pending` replaying pending code_changes.
- `get_recent_changes(file_path, limit=5) -> list[CodeChange]` - newest first.
- `save_session(session_id, agent, repo, *, context_payload="")` - upsert a
  session row (`INSERT OR REPLACE`), `context_payload` is a JSON string column.
- `end_session(session_id) -> str | None` - set `ended_at`; return the repo or
  None if the id is unknown.

Models `SessionMemory(project, summary, raw_turns, id, created_at)`,
`SessionNote(project, body, id, created_at)`, `CodeChange(commit_hash, file_path,
diff_summary, why, changed_at)` are in `axon.store.session_store`.

## Components

### SessionRepository Protocol + SqliteSessionRepository (`src/axon/store/session_repository.py`)

- `SessionRepository(Protocol)` declares the 9 methods.
- `SqliteSessionRepository(session)` - the CURRENT SessionStore session SQL moved
  verbatim (self -> self._session), including the `save_code_change` db-locked
  pending fallback. Behavior unchanged. `_save_code_change_inner` ->
  `save_code_change_inner`.

### PostgresSessionRepository (`src/axon/store/pg_session_repository.py`)

- `__init__(dsn)` - lazy asyncpg pool.
- `ensure_schema()` - idempotent; ports the baseline + 001 DDL:
  - `session_memory (id bigserial PRIMARY KEY, project text NOT NULL, summary
    text NOT NULL, raw_turns integer NOT NULL, created_at text NOT NULL)`
  - `session_note (id bigserial PRIMARY KEY, project text NOT NULL, body text
    NOT NULL, created_at text NOT NULL)`
  - `code_change (commit_hash text NOT NULL, file_path text NOT NULL,
    diff_summary text NOT NULL, why text NOT NULL DEFAULT '', changed_at text
    NOT NULL, PRIMARY KEY (commit_hash, file_path))`
  - `sessions (id text PRIMARY KEY, agent text NOT NULL, repo text NOT NULL,
    started_at text NOT NULL, ended_at text, context_payload text NOT NULL
    DEFAULT '{}')`
- Method ports: `?` -> `$1..`; `INSERT ... RETURNING id` for memory/note (vs
  SQLite `lastrowid`); `ON CONFLICT (commit_hash, file_path) DO UPDATE` for
  code_change and `ON CONFLICT (id) DO UPDATE` for sessions (vs `INSERT OR
  REPLACE`); `end_session` does a SELECT-then-UPDATE exactly as SQLite. Ordering
  by `created_at`/`changed_at DESC` is byte-identical (text ISO timestamps order
  the same under Postgres locale and SQLite BINARY - ASCII-only, the wave-2
  collation trap does not apply). No SQLite-lock fallback on Postgres
  (`save_code_change` == `save_code_change_inner`). `close()` closes the pool.

### SessionStore delegation (`src/axon/store/session_store.py`)

- Resolve the sessions backend internally via `load_runtime_config().sessions_backend`;
  lazy `_sessions()` accessor (mirrors `_decisions()`/`_graph()`): postgres ->
  `PostgresSessionRepository(pg_url)` + `ensure_schema`, else
  `SqliteSessionRepository(self)`.
- The 9 session methods become thin delegations with identical signatures.
- `drain_pending`'s `code_change` branch calls
  `(await self._sessions()).save_code_change_inner(...)`.
- `close()` also closes the sessions repository if it owns a Postgres pool.
- After this wave, every SessionStore data method delegates; the aiosqlite
  connection is used only by the Sqlite* repositories when a concern is on sqlite.

### Config (`RuntimeConfig.sessions_backend`)

- Defaulted trailing field `sessions_backend: str = "sqlite"`;
  `_resolve_sessions_backend` (env > toml > sqlite, validated {sqlite,postgres});
  `"sessions_backend"` in the toml allowlist. Default flips to postgres in the
  cutover task.

### Data-copy script (`scripts/migrate_sessions.py`)

- `copy_sessions(src_repo, dst_repo, *, projects) -> dict[str, int]` copying
  memories + notes (per project), code_changes (all), and sessions. Idempotent
  (upserts / RETURNING). A new `all_*` helper is added where a full scan is
  needed (mirrors `all_decisions`/`all_nodes`): `all_code_changes()`,
  `all_sessions()`; memories/notes copied per project via the existing getters
  with a high limit.

### Consolidation: AXON_DB_BACKEND (`src/axon/config/runtime.py`)

- Add a `_resolve_concern_backend(concern, overrides)` helper used by all four
  resolvers with precedence: `AXON_<CONCERN>_BACKEND` env > `AXON_DB_BACKEND`
  env > axon.toml `<concern>_backend` > axon.toml `db_backend` > `"postgres"`
  (all concerns now default postgres). The four existing
  `_resolve_<concern>_backend` functions delegate to it. `"db_backend"` is added
  to the toml allowlist. This lets `AXON_DB_BACKEND=sqlite` (or
  `[runtime] db_backend = "sqlite"`) flip ALL concerns at once for a full
  rollback, while a per-concern flag still overrides for one concern.
- The test conftest pin (already setting the four `AXON_*_BACKEND=sqlite`) keeps
  working; it can optionally be simplified to a single `AXON_DB_BACKEND=sqlite`.

## Cutover (data copy, then flip)

1. Bring up `axon-postgres`.
2. `python scripts/migrate_sessions.py` - copy memories/notes/code_changes/sessions.
3. Validate: Postgres counts match SQLite; `get_recent_changes` /
   `get_session_memories` return the same rows under `AXON_SESSIONS_BACKEND=postgres`.
4. Flip `sessions_backend = "postgres"`.
5. Rollback: `sessions_backend = "sqlite"` (or, post-consolidation,
   `AXON_DB_BACKEND=sqlite` to roll the whole step back); SQLite tables intact.

## Testing strategy

1. PostgresSessionRepository conformance (testcontainers[postgres]): save/get for
   memory, note, code_change (upsert dedup), session (upsert + end_session
   returns repo / None), ordering + limit, ensure_schema idempotent.
2. SessionStore delegation: `sessions_backend=postgres` routes the 9 methods to
   Postgres; `sqlite` to SQLite; `drain_pending` code_change replay uses the repo.
3. Config precedence: default sqlite, env override, toml override, unknown raises;
   AND the consolidation tier: `AXON_DB_BACKEND=sqlite` flips all four concerns,
   a per-concern flag overrides it, env beats toml.
4. Copy script: count parity; idempotent re-run.
5. The conftest autouse pin keeps the suite isolated (no per-file pins needed).

## Success criteria

1. `PostgresSessionRepository` passes conformance and matches SqliteSessionRepository.
2. `SessionStore` delegates all session methods by `sessions_backend`; consumers
   (pb.py, mcp/server.py) unchanged; SessionStore has no remaining direct SQL.
3. The copy script moves the data with count parity, idempotent.
4. `AXON_DB_BACKEND` flips all four concerns; per-concern flags still override.
5. SQLite remains a one-flag rollback for sessions and for the whole step.
