# Design: decisions/ADRs -> Postgres (dec-121 step 3, wave 3)

- Date: 2026-06-22
- Status: approved (brainstorming), pending spec review
- Implements: dec-121 step 3 (relational source of truth SQLite -> Postgres),
  WAVE 3 of 4 (decisions + ADRs)
- Builds on: file_index (wave 1) + graph (wave 2), merged
- Branch: `feat/decisions-postgres` (off `feat/rtkx-consume`)

## Goal

Move the decision knowledge base (the `decisions` and `adr` tables) from SQLite
to PostgreSQL behind a `DecisionRepository` Protocol, selectable by
`AXON_DECISIONS_BACKEND`, with the existing data copied over. `SessionStore`
keeps its decision/ADR method signatures but delegates to the configured
repository, so the 23 consumer call sites are unchanged. Session continuity
(memories/notes/code_changes/sessions) stays on SQLite (wave 4). Decisions store
their JSON as JSONB on Postgres so the find_* queries become native operators.

## Scope

In scope (this wave):

- A `DecisionRepository` Protocol (5 decision methods + 3 ADR methods).
- `SqliteDecisionRepository` (current SQL extracted) and
  `PostgresDecisionRepository` (asyncpg, JSONB + native JSON operators).
- `SessionStore` delegating its decision/ADR methods.
- `RuntimeConfig.decisions_backend` (env > axon.toml > default sqlite).
- A one-shot data-copy script (decisions + adr rows SQLite -> Postgres).
- The cutover (copy + flip) + a runbook note.

Out of scope (wave 4 / later):

- Session continuity (memories/notes/code_changes/sessions) - stays SQLite.
- `drain_pending` itself stays in `SessionStore` (it orchestrates pending ->
  writer dispatch); it delegates the ADR write to the repository.

## Decisions (from brainstorming)

1. **JSONB + GIN for decisions.frontmatter.** The Decision is serialized as JSON
   into `frontmatter`, and the find_* queries use `json_extract`/`json_each`.
   Storing it as `jsonb` (with a GIN index) makes those queries native and
   indexable, matching the dec-121 ADR's JSONB intent. The JSON round-trips
   identically (`json.dumps`/`json.loads` <-> jsonb).
2. **Per-concern flag `AXON_DECISIONS_BACKEND`** (env > axon.toml > default
   sqlite), flipped after validation; consolidates into `AXON_DB_BACKEND` at the
   end of step 3.
3. **Copy the data.** Decisions and ADRs are real persisted data (not a cache);
   a one-shot idempotent copy moves them.
4. **db-locked fallback stays SQLite-only.** `save_adr`'s pending-write fallback
   exists because SQLite serializes writers and can raise "database is locked".
   The SqliteDecisionRepository keeps it; the Postgres repository's `save_adr`
   just inserts (asyncpg pool, no SQLite-style lock). `drain_pending` (in
   SessionStore) keeps replaying pending ADRs via the repository's inner insert.

## The DecisionRepository contract

Extracted from SessionStore (the SQLite behavior is the reference):

- `save_decision(decision: Decision)` - upsert by id (SQLite `INSERT OR REPLACE`
  -> Postgres `ON CONFLICT (id) DO UPDATE`).
- `find_decisions_by_symbol(symbol_id) -> list[Decision]` - decisions whose
  `frontmatter.symbols` array contains `symbol_id`, newest first.
- `find_decision_by_git_hash(git_hash, *, repo=None) -> Decision | None` - newest
  decision matching `frontmatter.git_hash` (and optional `frontmatter.repo`).
- `find_decisions_by_repo(repo, limit=20) -> list[Decision]` - decisions matching
  `frontmatter.repo`, newest first, limited.
- `next_decision_id() -> str` - `dec-NNN` from `COUNT(*)+1`.
- `save_adr(adr: ADR) -> int` - insert an ADR, return its new id.
- `get_adrs(project, limit=10) -> list[ADR]` - ADRs for a project, newest first.
- `save_adr_inner(adr: ADR) -> int` - the no-fallback insert returning the id;
  used by `SessionStore.drain_pending` when replaying pending ADRs.

`Decision` (`axon.core.decision`) carries `judged: bool` and `validation_score:
float` INSIDE the model, so they live in the `frontmatter` JSON and round-trip
through JSONB automatically. Do NOT add a separate `judged` column or treat
`validation_score == 0.0` as a sentinel (CLAUDE.md / dec-109): `judged` is the
canonical scored flag and must survive the round-trip as a real boolean.

## Components

### DecisionRepository Protocol + SqliteDecisionRepository (`src/axon/store/decision_repository.py`)

- `DecisionRepository(Protocol)` declares the 8 methods.
- `SqliteDecisionRepository(session)` - the CURRENT SessionStore decision/ADR SQL
  moved verbatim (self -> self._session), including the `save_adr` db-locked
  pending fallback and the SQLite JSON functions. Behavior unchanged.

### PostgresDecisionRepository (`src/axon/store/pg_decision_repository.py`)

- `__init__(dsn)` - lazy asyncpg pool.
- `ensure_schema()` - idempotent:
  - `decisions (id text PRIMARY KEY, frontmatter jsonb NOT NULL, body text,
    vault_path text, created_at text NOT NULL)` + `CREATE INDEX ... USING gin
    (frontmatter)`.
  - `adr (id bigserial PRIMARY KEY, project text NOT NULL, title text NOT NULL,
    context text NOT NULL, decision text NOT NULL, rationale text NOT NULL,
    created_at text NOT NULL)`.
- Method ports:
  - `save_decision`: `INSERT INTO decisions (...) VALUES ($1, $2::jsonb, $3, $4,
    $5) ON CONFLICT (id) DO UPDATE SET ...`. frontmatter passed as a json string
    cast to jsonb.
  - `find_decisions_by_symbol`: `... WHERE EXISTS (SELECT 1 FROM
    jsonb_array_elements_text(frontmatter->'symbols') v WHERE v = $1) ORDER BY
    created_at DESC`.
  - `find_decision_by_git_hash`: `... WHERE frontmatter->>'git_hash' = $1
    [AND frontmatter->>'repo' = $2] ORDER BY created_at DESC LIMIT 1`.
  - `find_decisions_by_repo`: `... WHERE frontmatter->>'repo' = $1 ORDER BY
    created_at DESC LIMIT $2`.
  - `next_decision_id`: `SELECT count(*) FROM decisions` -> `dec-{n+1:03d}`.
  - `save_adr_inner` / `save_adr`: `INSERT INTO adr (...) VALUES ($1..$6)
    RETURNING id` (Postgres replaces SQLite `lastrowid`). The Postgres `save_adr`
    has no SQLite-lock fallback; it equals `save_adr_inner`.
  - `get_adrs`: `SELECT id, project, title, context, decision, rationale,
    created_at FROM adr WHERE project=$1 ORDER BY created_at DESC LIMIT $2`.
  - Rows -> `Decision(**json.loads(...))` (asyncpg returns jsonb as a dict or
    str depending on codec; normalize: if the driver returns a str, json.loads
    it; if a dict, use it directly) and `ADR(...)` exactly as SQLite does.
  - `close()` closes the pool.

### SessionStore delegation (`src/axon/store/session_store.py`)

- Resolve the decisions backend internally (so the 23 consumers and
  `SessionStore(db_path)` are unchanged): `load_runtime_config().decisions_backend`.
  Lazily build `SqliteDecisionRepository(self)` or `PostgresDecisionRepository(pg_url)`
  (+ `ensure_schema`) in a `_decisions()` accessor (mirrors `_graph()`).
- The decision/ADR methods become thin delegations with identical signatures.
- `drain_pending`'s ADR branch calls `(await self._decisions()).save_adr_inner(...)`.
- `close()` also closes the decisions repository if it owns a Postgres pool.
- Sessions/memories/notes/code_changes stay on the aiosqlite connection.

### Config (`RuntimeConfig.decisions_backend`)

- Defaulted trailing field `decisions_backend: str = "sqlite"`;
  `_resolve_decisions_backend` (env > toml > sqlite, validated {sqlite,postgres});
  `"decisions_backend"` added to the toml allowlist. Default flips to postgres
  only in the cutover task.

### Data-copy script (`scripts/migrate_decisions.py`)

- `copy_decisions(src_repo, dst_repo) -> tuple[int, int]` (decisions, adrs).
  Reads all decisions (a new `all_decisions()` helper or `find_decisions_by_repo`
  per repo - see Open question) and all ADRs from SQLite, writes via
  `save_decision` / `save_adr_inner` to Postgres. Idempotent (save_decision
  upserts; ADRs are insert-only - guard re-runs by truncating adr first or
  skipping if already populated).

## JSONB codec note

asyncpg returns `jsonb` columns as Python objects only if a codec is set;
by default it returns the raw JSON string. The repository sets a jsonb codec
(`await conn.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads,
schema='pg_catalog')` on pool init) so reads yield dicts and writes accept dicts,
OR it passes/reads JSON strings explicitly. The plan picks one and applies it
consistently; either way `Decision(**data)` receives a dict.

## Data flow

Capture (`adr/inference.py`, `hooks/git_event.py`, `mcp/server.py`, etc.) calls
`store.save_decision` / `store.save_adr` -> delegated to the configured
repository. Recall (`recall/strategy.py`, `pb.py`, `mcp`) calls the find_*
methods -> delegated. The backend is chosen once in SessionStore.

## Error handling

- Unknown `decisions_backend`: `ValueError` at config load.
- postgres unreachable: asyncpg pool errors on first use (no silent fallback).
- `ensure_schema` idempotent.
- `save_adr` SQLite-lock fallback: SqliteDecisionRepository only; Postgres
  inserts directly.
- `judged`/`validation_score` preserved as real JSON values via JSONB round-trip.

## Cutover (data copy, then flip)

1. Bring up `axon-postgres`.
2. `python scripts/migrate_decisions.py` - copy decisions + ADRs.
3. Validate: Postgres `decisions`/`adr` counts match SQLite; a `find_decisions_by_repo`
   and a `find_decision_by_git_hash` return the SAME decisions under
   `AXON_DECISIONS_BACKEND=postgres`; a spot-check confirms a decision's `judged`
   value survived.
4. Flip `decisions_backend = "postgres"` in `axon.toml`.
5. Rollback: set `sqlite`; the SQLite decisions/adr tables are untouched.

## Testing strategy

1. PostgresDecisionRepository conformance (testcontainers[postgres], mirrors
   SqliteDecisionRepository): save_decision upsert; find_decisions_by_symbol
   (JSONB array contains); find_decision_by_git_hash (with/without repo);
   find_decisions_by_repo (order + limit); next_decision_id; save_adr returns id
   + get_adrs order/limit; a Decision with `judged=True` and a non-zero
   `validation_score` round-trips with both preserved; ensure_schema idempotent.
2. SessionStore delegation: `decisions_backend=postgres` routes the
   decision/ADR methods to Postgres; `sqlite` routes to SQLite; sessions/memories
   unaffected; `drain_pending` ADR replay uses the repository.
3. Config precedence (no live backend): default sqlite, env override, toml
   override, unknown raises.
4. Copy script: count parity; idempotent re-run.

## Success criteria

1. `PostgresDecisionRepository` passes conformance and matches
   `SqliteDecisionRepository` (upsert, JSON queries, ADR id, ordering, and the
   `judged`/`validation_score` round-trip).
2. `SessionStore` delegates the decision/ADR methods by `decisions_backend`; the
   23 consumers are unchanged; sessions/memories stay SQLite.
3. The copy script moves decisions + ADRs with count parity and is idempotent;
   find_* return the same results on Postgres as on SQLite.
4. SQLite remains default and working until the flip; rollback is one config line.

## Open question for the plan

`SqliteDecisionRepository` has no `all_decisions()` today (only find-by-repo /
symbol / hash). The copy script needs every decision. The plan adds a small
`all_decisions()` to BOTH repositories (a plain `SELECT frontmatter FROM
decisions ORDER BY created_at` -> `list[Decision]`), used by the copy and
potentially future full-scan consumers, mirroring the graph's `all_nodes`.
