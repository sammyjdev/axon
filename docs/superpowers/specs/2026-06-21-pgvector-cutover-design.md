# Design: pgvector cutover - make pgvector the active vector backend

- Date: 2026-06-21
- Status: approved (brainstorming), pending spec review
- Implements: dec-121 (unify persistence on Postgres), step 2 (blue/green vectors + cutover)
- Builds on: the 2026-06-21 pgvector-vector-store spec (step 1, merged)
- Branch: `feat/pgvector-cutover` (off `feat/rtkx-consume`)

## Goal

Make `pgvector` the active vector backend for AXON, selected by a config
setting, after validating a real re-index of the live vault at recall parity
with Qdrant. Qdrant stays intact and reachable for rollback until dec-121 step 5
removes it. This step changes which backend production uses; it does not touch
the relational store, Redis, or the graph.

## Scope

In scope (this cycle):

- A `vector_backend` setting on `RuntimeConfig`, sourced from `axon.toml`
  (`[runtime] vector_backend`) with the `AXON_VECTOR_BACKEND` env var as an
  override, defaulting to `pgvector` once the cutover gate passes.
- `make_vector_store()` reads `runtime.vector_backend` instead of reading the
  env var directly (the env override flows through `load_runtime_config`).
- `doctor` surfaces the active vector backend and pgvector connectivity.
- A cutover runbook (the re-index -> validate -> flip -> rollback sequence).
- A parity check (count + spot-search) of pgvector vs Qdrant for the live ctx.

Out of scope (later dec-121 cycles):

- SQLite -> Postgres for the relational source of truth (step 3).
- Replacing the Redis subgraph cache (step 4).
- Removing Qdrant / Redis from runtime config and docker-compose (step 5).
- `src/axon/benchmark/retrieval.py` direct Qdrant usage (offline benchmark, not
  on the production path; cleaned up separately).

## Decisions (from brainstorming)

1. **Migration mechanism: re-index from the vault (re-embed).** Run
   `AXON_VECTOR_BACKEND=pgvector axon index <vault> --ctx <ctx>` to re-embed the
   source vault directly into pgvector. AXON already re-indexes from source; the
   live data is tiny (Phase 0 confirmed only the `personal` ctx holds data), so
   re-embedding on GPU is seconds. No vector-copy script, no cross-backend
   payload-format handling. Cost: requires the embedder model + the vault
   present, which the cutover host has.
2. **Cutover mechanism: a config setting in `axon.toml`.** Add
   `[runtime] vector_backend`, read into `RuntimeConfig.vector_backend`, with
   `AXON_VECTOR_BACKEND` still overriding. Cutover = change one config line;
   rollback = change it back. Explicit per-install and visible in `doctor`.
   Preferred over a hidden code-default flip (breaks opaquely if Postgres is
   down) and over env-only opt-in (pgvector never becomes the official path).

## Key precondition (verified during design)

The production index and search paths are backend-agnostic: only
`src/axon/store/vector_store.py` (the Qdrant backend itself),
`src/axon/benchmark/recall.py` (the recall gate, already backend-parametric from
step 1), and `src/axon/benchmark/retrieval.py` (an offline benchmark) import
`qdrant_client`. `index_path` and its crash-safety / orphan-reconcile logic
(pending sentinel, per-file `delete_by_file`, subtree-scoped D6 cleanup) call
the store only through the `VectorStore` interface, which `PgVectorStore`
implements. So the vault re-index runs on pgvector with no structural code
change; step 2 is config + validation + cutover + runbook.

## Components

### `RuntimeConfig.vector_backend` (`src/axon/config/runtime.py`)

- A new field `vector_backend: str` on the frozen `RuntimeConfig` dataclass,
  placed next to `pg_url` / `qdrant_url`.
- `load_runtime_config()` populates it with this precedence:
  `os.environ.get("AXON_VECTOR_BACKEND")` (if set and non-empty) ->
  the `axon.toml` `[runtime] vector_backend` value -> the default.
- The default is `"qdrant"` until the cutover task, which flips it to
  `"pgvector"` (the final step of the plan, gated on validation).
- The value is normalized (`.strip().lower()`) and constrained to
  `{"qdrant", "pgvector"}`; an unknown value is a clear startup error, not a
  silent fallback.

### `make_vector_store()` (`src/axon/store/vector_store_factory.py`)

- Change the single line that reads the env var:
  `backend = os.environ.get("AXON_VECTOR_BACKEND", "qdrant").strip().lower()`
  becomes `backend = rt.vector_backend`.
- Everything else is unchanged: `pgvector` -> `PgVectorStore(dsn=rt.pg_url)`,
  otherwise `VectorStore(url=rt.qdrant_url)`. Callers are untouched.
- The env override still works for callers that pass no runtime, because
  `load_runtime_config()` applies the env precedence. The step-1 recall gate
  (which sets `AXON_VECTOR_BACKEND=pgvector` and calls `make_vector_store()`)
  keeps working.

### `doctor` (`src/axon/cli/pb.py`)

- The doctor report gains a line for the active vector backend
  (`vector backend: pgvector` / `qdrant`) under Presence, and a Liveness probe
  for the selected backend: for pgvector, a cheap `SELECT 1` against `pg_url`;
  for qdrant, the existing reachability check. A down backend reports
  `down (<reason>)` without aborting the rest of doctor.

### Cutover runbook (`docs/MIGRATION.md`)

- Document the exact sequence: (1) bring up `axon-postgres`; (2)
  `AXON_VECTOR_BACKEND=pgvector axon index <vault> --ctx <ctx>` for each ctx with
  data; (3) run the parity check; (4) run the recall gate; (5) flip
  `vector_backend = "pgvector"` in `axon.toml`; (6) verify `doctor`. Include the
  rollback (set `vector_backend = "qdrant"`; Qdrant data is untouched) and a note
  that Qdrant is retired only in step 5.

### Parity check (`scripts/verify_migration.py`)

- Add a pgvector-vs-Qdrant parity mode: for each target ctx, compare the Qdrant
  point count to the pgvector `embeddings` row count for that ctx, and run a
  handful of spot-search queries through both backends asserting the same top
  hit. Prints a clear PASS/FAIL summary. This is a manual cutover aid, not a CI
  test (it needs the live vault + both backends populated).

## Validation / acceptance gate (to flip the default)

The default flips to `pgvector` only after all of:

1. The live vault re-indexes into pgvector cleanly via the production
   `axon index` path (no errors; row count non-zero).
2. The recall gate is green on pgvector (`AXON_VECTOR_BACKEND=pgvector
   AXON_RUN_RECALL=1`) - already wired and green from step 1.
3. The parity check passes: per-ctx count parity and spot-search top-hit parity
   vs Qdrant.

Automated tests cover the config precedence and the factory selection; the
real-vault re-index + parity are runbook steps the operator runs once at cutover
(they need the actual vault and GPU).

## Data flow

`axon index <vault> --ctx <ctx>` -> `make_vector_store()` reads
`runtime.vector_backend` -> when `pgvector`, returns `PgVectorStore` ->
`index_path` embeds and `upsert_batch`/`delete_by_file` through the interface ->
search at query time uses the same selected backend. Nothing in the index or
search code branches on the backend; the selection is entirely in the factory.

## Error handling

- Unknown `vector_backend` value: explicit `ValueError` at config load with the
  allowed set, not a silent default.
- pgvector selected but Postgres unreachable: `PgVectorStore` surfaces the pool
  connection error on first use (no silent fallback to Qdrant - the backend is an
  explicit choice). `doctor` shows it as `down`.
- Env override precedence: `AXON_VECTOR_BACKEND` always wins over `axon.toml`,
  so an operator can force a backend for one command without editing config.

## Testing strategy

1. Config precedence (`tests/config/`): `vector_backend` defaults correctly;
   `axon.toml [runtime] vector_backend` is read; `AXON_VECTOR_BACKEND` overrides
   the toml value; an unknown value raises. No model load, no live backend.
2. Factory (`tests/store/test_vector_store_factory.py`): `make_vector_store()`
   selects by `runtime.vector_backend`; the env-override path still selects
   pgvector. The existing default test is updated when the default flips.
3. doctor (`tests/cli/`): the report shows the active vector backend; a down
   pgvector is reported, not raised. Backend probes are mocked (no live DB).
4. Recall gate: unchanged from step 1; remains the cross-backend parity gate.

## Success criteria

1. `make_vector_store()` selects the backend from `axon.toml`, with
   `AXON_VECTOR_BACKEND` overriding, and `doctor` shows the active backend.
2. The live vault re-indexes into pgvector via the production path and the
   recall gate + parity check pass.
3. After the flip, AXON uses pgvector by default; setting
   `vector_backend = "qdrant"` (or the env override) rolls back instantly with
   Qdrant data intact.
4. Qdrant remains present and functional as the rollback target (its removal is
   step 5).
