# Agent Backlog

Candidate work items for the agentic builder loop (`agent-task` / `agent-run`).
Each item is independently pickable: scope, acceptance criteria, and test plan
are self-contained. Shared context lives in the linked spec.

Status legend: `ready` (pickable now) | `blocked` (waiting on a dep) |
`in-progress` | `done`. Priority: P0 (false safety signal) > P1
(correctness/high-impact) > P2 (safety) > P3 (quality). Size: S (<1d) /
M (1-2d) / L (multi-day).

Epic: **Benchmark-guided AXON evolution** -
[`docs/superpowers/specs/2026-07-11-benchmark-guided-axon-evolution-design.md`](superpowers/specs/2026-07-11-benchmark-guided-axon-evolution-design.md).
Fix confirmed contract gaps before adding retrieval techniques. Recommended order:
**L1-0** first; **OPS-1**, **HTTP-1**, and **EMB-1** are independent; **L1-1**
waits for an owner decision about index authority and freshness.

---

## L1-0 - Make L1-full revalidation honest

- Priority: P0 | Size: S | Status: ready | Depends-on: none
- Spec: benchmark-guided evolution, "Task L1-0"

**Problem.** `l1_full()` returns unconditional stub success and
`axon adr validate-drafts` updates `last_l1_full_at`, clearing `stale-pending`
without structural validation.

**Acceptance criteria.**
- [ ] An unavailable L1-full backend returns `indeterminate` and leaves
      `last_l1_full_at` unchanged.
- [ ] The draft remains active and `stale-pending`; it is not validated,
      promoted, or demoted.
- [ ] CLI and git-event callers share the same tri-state transition and report
      `validated`, `demoted`, and `indeterminate` separately.
- [ ] Lookup failures fail safe as `indeterminate`; L1-light is unchanged.

**Files.** `src/axon/adr/gates/l1.py`, `src/axon/cli/pb.py`,
`src/axon/hooks/git_event.py`, focused ADR/CLI/doctor tests.

**Test plan.** Start with a regression test for the false timestamp update, then
run `rtk pytest tests/adr/gates/test_l1.py tests/adr/test_clis.py
tests/doctor/test_checks_adr.py -q`.

---

## L1-1 - Connect L1-full to the structural index

- Priority: P1 | Size: M | Status: blocked | Depends-on: L1-0 + owner decision
- Spec: benchmark-guided evolution, "Task L1-1"

**Blocker.** Decide which existing Postgres structure is authoritative for
symbol existence, how repository identity scopes keys, and how index freshness
is proven for the evaluated revision. Missing, ambiguous, or stale evidence must
remain `indeterminate`.

**Acceptance criteria.**
- [ ] The owner decision records index authority, repository scope, freshness,
      and path/symbol candidate semantics.
- [ ] Present candidates in the correct repository return `validated`.
- [ ] Absent candidates in a proven-current index return `demoted` with details.
- [ ] Missing, stale, ambiguous, or unavailable index state returns
      `indeterminate` without renewing the timestamp.
- [ ] CLI and hooks use the L1-0 transition; tests cover cross-repo collisions.

**Files.** Selected Postgres graph repository, `src/axon/adr/gates/l1.py`, and
focused repository/gate tests.

**Test plan.** Contract tests with fake lookups for present, absent, unavailable,
stale, and cross-repository collision cases, followed by the L1-0 test command.

---

## OPS-1 - Align the operational contract with Postgres

- Priority: P1 | Size: S | Status: ready | Depends-on: none
- Spec: benchmark-guided evolution, "Task OPS-1"

**Problem.** dec-121 and `AGENTS.md` define unified Postgres storage, while active
rules, health/help output, and operational docs still advertise SQLite, Redis,
or Qdrant as current runtime components.

**Acceptance criteria.**
- [ ] `RULES.md` and active runtime configuration no longer promise SQLite
      rollback or retired backend choices.
- [ ] `axon health` and help name the Postgres, pgvector, vault, and git probes
      they actually execute.
- [ ] Active operational docs and runtime docstrings stop instructing users to
      configure SQLite, Redis, or Qdrant.
- [ ] Historical ADRs and migration plans remain unchanged.
- [ ] Health-label tests pass and a scoped retired-term scan is reviewed.

**Files.** `RULES.md`, runtime config, health/help surfaces,
`docs/SECOND_BRAIN.md`, relevant runtime docstrings and tests.

**Test plan.** Run focused MCP/CLI tests and the scoped scan defined in the spec.

---

## HTTP-1 - Correlate HTTP evaluation with retrieval

- Priority: P1 | Size: M | Status: ready | Depends-on: none
- Spec: benchmark-guided evolution, "Task HTTP-1"

**Problem.** `POST /v1/chat/completions` retrieves context and records usage but
does not connect the HTTP response, retrieval stage, and `RecallRecord` by a
request identifier.

**Acceptance criteria.**
- [ ] Every context-enabled response has a non-empty `trace_id` shared with
      exactly one `RecallRecord`.
- [ ] The existing trace store records one retrieval stage with non-negative
      duration, hit count, and strategy metadata when available.
- [ ] Context-disabled requests preserve `contexts=[]` and do not fabricate
      retrieval hits or strategy.
- [ ] Telemetry stores neither raw query nor retrieved segment text.
- [ ] Telemetry persistence remains non-fatal and the HTTP contract remains
      additive for GNOMON.

**Files.** `src/axon/http/app.py`, existing trace/recall telemetry models, and
focused HTTP/observability tests.

**Test plan.** TDD both `include_context` branches, then run
`rtk pytest tests/http/test_chat_completions.py
tests/observability/test_recall_telemetry.py -q`. External GNOMON validation
remains blocked until this task is complete.

Epic: **Postgres storage hardening** -
[`docs/superpowers/specs/2026-06-22-pg-storage-hardening-design.md`](superpowers/specs/2026-06-22-pg-storage-hardening-design.md)
(findings F1-F8). Recommended first wave (no soft deps): MS-2, MS-3, MS-5.

---

## MS-2 - Make `migrate_sessions` copy idempotent (and tell the truth in the docstring)

- Priority: P1 | Size: S | Status: ready | Depends-on: none
- Finding: F2 (ours) | Spec: pg-storage-hardening F2

**Problem.** `scripts/migrate_sessions.py` docstring claims "idempotent", but
`save_session_memory` / `save_note` are plain `INSERT ... RETURNING id` with no
natural-key constraint, so re-running the copy **duplicates** every memory and
note. Only `code_change` (composite PK) and `sessions` (text PK) actually upsert.

**Acceptance criteria.**
- [ ] Re-running `copy_sessions(src, dst)` twice leaves identical row counts in
      `session_memory` and `session_note` (no duplicates).
- [ ] Idempotency achieved by a natural-key dedup: add a UNIQUE constraint
      (`session_memory(project, summary, raw_turns, created_at)`,
      `session_note(project, body, created_at)`) in `ensure_schema` + matching
      `ON CONFLICT DO NOTHING`; OR a script-level "skip if exists" check. Pick one
      and document why.
- [ ] The docstring/comment accurately states what is and isn't idempotent.
- [ ] Same fix audited for `migrate_decisions` / `migrate_graph` if they share the
      gap; if they don't, note it.

**Files.** `scripts/migrate_sessions.py`, `src/axon/store/pg_session_repository.py`
(ensure_schema + the two inserts), `tests/scripts/test_migrate_sessions.py`.

**Test plan.** Extend the FakeRepo test: run `copy_sessions` twice, assert counts
stable; add a testcontainers test that double-runs against real Postgres.

---

## MS-3 - Atomic `end_session` + non-destructive `save_session` re-save

- Priority: P1 | Size: M | Status: ready | Depends-on: none
- Finding: F3 (inherited; needs a behavior decision) | Spec: pg-storage-hardening F3

**Problem.** Two coupled issues, both ports of SQLite `INSERT OR REPLACE`:
(a) `PostgresSessionRepository.end_session` does `SELECT repo` then `UPDATE` on a
pooled connection with no transaction - a concurrent `save_session` can interleave;
(b) `save_session` `ON CONFLICT (id) DO UPDATE SET started_at=excluded.started_at,
ended_at=excluded.ended_at` overwrites the original `started_at` with `now()` and
clears `ended_at` on any re-save (reconnect, or the copy script re-running),
silently destroying session history.

**Decision required (record in the PR).** Is re-`save_session` on an existing id
meant to (i) re-open the session, or (ii) be a no-op preserving the original?
Default recommendation: preserve `started_at`/`ended_at`; only update
`agent`/`repo`/`context_payload`.

**Acceptance criteria.**
- [ ] `end_session` is a single atomic statement
      (`UPDATE sessions SET ended_at=$1 WHERE id=$2 AND ended_at IS NULL RETURNING repo`,
      returning `None` when the id is unknown) - no SELECT-then-UPDATE.
- [ ] `save_session` no longer resets `started_at`/`ended_at` for an existing id
      (per the decision above).
- [ ] The SQLite impl is updated to match the chosen semantics (both backends
      agree); the conformance test asserts parity.
- [ ] A regression test reproduces the old destructive re-save and proves the fix.

**Files.** `src/axon/store/pg_session_repository.py`,
`src/axon/store/session_repository.py`, `tests/store/test_pg_session_repository.py`.

**Test plan.** testcontainers: save_session(id) -> end_session(id) -> save_session(id)
again; assert started_at preserved and ended_at not silently cleared; assert
end_session("missing") is None.

---

## MS-5 - Coroutine-safe lazy pool / repository init

- Priority: P2 | Size: S | Status: ready | Depends-on: none
- Finding: F5 (inherited; repo-wide) | Spec: pg-storage-hardening F5

**Problem.** `PostgresSessionRepository._ensure_pool` and
`SessionStore._sessions()` (and the sibling `_graph()` / `_decisions()`) do an
unguarded check-then-assign across an `await`. Two coroutines can both see `None`,
both `create_pool` / `ensure_schema`, and orphan one pool (its connections never
closed).

**Acceptance criteria.**
- [ ] `_ensure_pool` guards the lazy init with an `asyncio.Lock` (double-checked
      inside the lock); concurrent callers get the same single pool.
- [ ] `SessionStore._sessions()` / `_graph()` / `_decisions()` guard their lazy
      init (reuse `self._lock` or a dedicated init lock).
- [ ] A test spawns N concurrent first-callers and asserts exactly one pool /
      one `ensure_schema` call.

**Files.** `src/axon/store/pg_session_repository.py`,
`src/axon/store/session_store.py` (+ `pg_graph_repository.py` /
`pg_decision_repository.py` for the same `_ensure_pool` pattern),
`tests/store/`.

**Test plan.** `asyncio.gather(*[store._sessions() for _ in range(20)])` with a
monkeypatched repo counting constructions; assert count == 1.

---

## MS-4 - Postgres `schema_version` + versioned migration runner

- Priority: P2 | Size: M | Status: ready | Depends-on: none (enables MS-1)
- Finding: F4 (inherited) | Spec: pg-storage-hardening F4

**Problem.** SQLite has a real migration system (`_apply_migrations` +
`schema_version` table, `.sql` files). The Postgres path creates schema with
inline `CREATE TABLE IF NOT EXISTS` and has NO version tracking, so any future
shape change (e.g. MS-1's `text`->`timestamptz`) cannot be applied to an existing
table and drift is silent (`IF NOT EXISTS` is a no-op once the table exists).

**Acceptance criteria.**
- [ ] A reusable Postgres migration runner: a `schema_version(version, applied_at)`
      table + apply-in-order of versioned migrations, idempotent, mirroring the
      SQLite `_apply_migrations` contract.
- [ ] The session tables' baseline DDL is expressed as migration `0001` and
      applied through the runner (replacing the inline `ensure_schema` body, or
      `ensure_schema` delegates to the runner).
- [ ] Running twice is a no-op; a new migration file is picked up on next start.
- [ ] Follow-up noted: graph/decisions/file_index Postgres paths can adopt the
      same runner (not required in this unit).

**Files.** new `src/axon/store/pg_migrations.py` (or similar) +
`src/axon/store/migrations/pg/0001_*.sql`, `src/axon/store/pg_session_repository.py`,
`tests/store/`.

**Test plan.** testcontainers: fresh DB -> runner applies 0001 -> tables exist,
`schema_version` has one row; second run adds nothing; a dummy 0002 is applied
exactly once.

---

## MS-1 - Session timestamps: `text` -> `timestamptz`

- Priority: P1 | Size: M | Status: blocked (soft) | Depends-on: MS-4 (soft)
- Finding: F1 (inherited) | Spec: pg-storage-hardening F1

**Problem.** `created_at` / `changed_at` / `started_at` / `ended_at` are stored as
`text` ISO strings and queried with `ORDER BY ... DESC`. Lexicographic order ==
chronological order ONLY while every value is uniform UTC with the same offset
suffix and fractional precision; a single naive/non-UTC/`Z`-vs-`+00:00` value
silently corrupts ordering. `timestamptz` is the same 8 bytes, normalizes to UTC,
and sorts/indexes as an integer.

**Acceptance criteria.**
- [ ] Session-table timestamp columns are `timestamptz NOT NULL` (Postgres).
- [ ] The repo passes `datetime` objects to asyncpg (no `.isoformat()` string
      intermediary) and reads back `datetime` directly (drop `datetime.fromisoformat`).
- [ ] Both backends still produce identical Pydantic models from a round-trip
      (SQLite stays TEXT internally; the conformance/parity tests pass).
- [ ] The column change ships as a versioned migration (via MS-4) with a
      `USING created_at::timestamptz` cast; if MS-4 is not yet merged, ship a
      guarded one-off migration and note the dependency.
- [ ] Follow-up noted for graph/decisions/file_index timestamp columns.

**Files.** `src/axon/store/pg_session_repository.py`, the MS-4 migration dir,
`tests/store/test_pg_session_repository.py`.

**Test plan.** testcontainers: insert rows out of chronological order, assert
`get_session_memories` / `get_recent_changes` return strict chronological DESC;
assert a mixed-offset value would sort correctly (it can't be inserted as text now).

---

## MS-7 - Migration validation beyond row counts (content checksum)

- Priority: P3 | Size: M | Status: ready | Depends-on: MS-1 (soft) | Finding: F7 (ours)

**Problem.** The cutover gate validates SQLite->Postgres copies by row COUNT only.
The boundary silently coerces types (text timestamps, int-vs-bool, affinity-dirty
data); counts stay equal while content diverges (AWS DMS / Stripe / gh-ost all
checksum, not count).

**Acceptance criteria.**
- [ ] A reusable verifier: per-table deterministic checksum over PK-ordered,
      type-normalized columns (hash each row, aggregate per table), comparing
      source vs target; reports first mismatching PK.
- [ ] Wired into `scripts/migrate_sessions.py` as a `--verify` step (and usable by
      the other `migrate_*` scripts).
- [ ] A test injects a deliberate value corruption and asserts the verifier flags
      it (count parity alone would pass).

**Files.** new `scripts/_migration_verify.py` (or `src/axon/store/`),
`scripts/migrate_sessions.py`, `tests/scripts/`.

**Test plan.** Fake src/dst repos with one mutated row -> verifier returns a
mismatch with the offending PK; identical data -> verifier returns OK.

---

## MS-6 - Unify `save_code_change` error handling; dedupe SQLite helpers

- Priority: P3 | Size: S | Status: ready | Depends-on: none | Finding: F6 (inherited)

**Problem.** `SessionStore.save_code_change` catches `aiosqlite.OperationalError`
even when the backend is Postgres (dead code on that path; a transient Postgres
error escapes the pending fallback). The pending fallback is SQLite-specific and
is duplicated between `SessionStore.save_code_change` and
`SqliteSessionRepository.save_code_change`; `_is_db_locked` / `_pending_paths` /
`_warnings_log` are defined in both modules and can drift.

**Acceptance criteria.**
- [ ] The db-locked pending fallback lives in exactly one place (the SQLite repo);
      `SessionStore.save_code_change` is a thin delegation with no SQLite-specific
      `except`.
- [ ] On the Postgres path, a transient error surfaces or is handled by a
      Postgres-appropriate policy (documented), not swallowed by a dead
      `aiosqlite` catch.
- [ ] `_is_db_locked` / `_pending_paths` / `_warnings_log` are defined once
      (shared module) and imported by both.

**Files.** `src/axon/store/session_store.py`,
`src/axon/store/session_repository.py`, a shared `_util`/`pending` module,
`tests/store/`.

**Test plan.** Simulate a locked SQLite write -> pending file written + warning
emitted (unchanged behavior); Postgres path raises/handles per policy, asserting
no `aiosqlite` catch is reached.

---

## MS-8 - Type the `SessionRepository` Protocol + shared column/SQL helpers (anti-drift)

- Priority: P3 | Size: M | Status: ready | Depends-on: none | Finding: F8 (inherited)

**Problem.** Four Protocol methods take bare `mem`/`note`/`change` (implicit
`Any`), so `@runtime_checkable` + mypy can't verify the impls satisfy the contract.
And the two hand-written SQL implementations can drift (paramstyle `?` vs `$1`,
upsert grammar, column lists). Market exemplar `chaosblade` mitigates this with a
shared column-list + helper module and per-method canonical-SQL docstrings.

**Acceptance criteria.**
- [ ] All `SessionRepository` Protocol methods are fully typed
      (`mem: SessionMemory`, `note: SessionNote`, `change: CodeChange`); mypy
      verifies both impls conform.
- [ ] Column lists / row->model mapping shared between the two impls (single
      source of truth) so a schema change touches one place.
- [ ] Each Protocol method documents its canonical intent (the SQL semantics both
      impls must honor).

**Files.** `src/axon/store/session_repository.py`,
`src/axon/store/pg_session_repository.py`, optional shared `_session_columns.py`.

**Test plan.** `mypy` clean on the three files; a structural test that both impls
are `isinstance(..., SessionRepository)` and round-trip each model identically.

---

## MS-9 - Clear pre-existing test debt + widen the CI / loop gate

- Priority: P2 | Size: L | Status: ready | Depends-on: none | Finding: infra (loop gate)

**Problem.** The full `pytest -q` is RED on master, but CI never caught it:
`.github/workflows/ci.yml` only runs `pytest tests/router tests/resilience` (the
`ruff` job is likewise scoped to router+resilience, with a TODO noting ~22
pre-existing lint findings). The loop gate is therefore scoped to a green subset
(`router + resilience + store + scripts`). The debt:
- `tests/config/*`: assert outdated defaults - e.g.
  `test_runtime_defaults_to_full_local_mode` expects `full-local` but the code
  defaults to `hybrid-local` (solo-dev profile); also runtime_toml / profiles /
  setup_script / configure.
- `tests/benchmark/*`: counts depend on the active provider profile.
- `tests/doctor` + `tests/hooks`: TTY + Windows exec-bit fragility (4 already
  fixed on `chore/axon-loop-onboarding`; verify none remain).
- ~22 ruff findings (I001/E501/F401) outside router+resilience (scripts/,
  src/axon/store, tests/store).

**Decision required (record per test).** For each failing test, decide whether
the TEST is outdated or the CODE drifted - do NOT blindly skip. Headline call:
is the default runtime mode meant to be `full-local` or `hybrid-local`? Fix the
wrong side.

**Acceptance criteria.**
- [ ] `pytest -q` green on a clean checkout (each failure fixed with the
      test/code mismatch resolved, or a recorded justified `skipif`).
- [ ] `ruff check .` green (clear the ~22 findings).
- [ ] `ci.yml` widened to run the broader suite + `ruff check .` (or a documented
      green superset) so the debt cannot silently regrow.
- [ ] The loop `gate_cmd` in `.claude/loop.yaml` widened to match the new CI gate.

**Files.** `tests/config/*`, `tests/benchmark/*`, `src/axon/config/runtime.py`
(if the default is the wrong side), `.github/workflows/ci.yml`,
`.claude/loop.yaml`, the ~8 lint-debt files.

**Test plan.** `pytest -q` green; `ruff check .` green; CI runs both on PR.

---

## LR-1 - Live operational verification of the dec-122 hosted local-roles backend

- Priority: P2 | Size: S | Status: ready | Depends-on: none
- Decision: dec-122 (accepted; wired on master, `USE_HOSTED_LOCAL_ROLES=True`)

**Problem.** dec-122's production wiring is implemented and is the default
(scoring -> `groq/openai/gpt-oss-120b`, compressor -> `cerebras/gpt-oss-120b`, via
`axon.router.llm_backend`), but it was never smoke-tested end-to-end against the
real hosted providers. The eval harness (`benchmark/model_eval`) scored the models
in isolation; the live production path - real Groq/Cerebras keys, the per-handle
fallback chain (provider A -> B -> anthropic), and the `ctx=work` block - has no
runtime confirmation. This is the one open gap left after dec-121/dec-122.

**Acceptance criteria.**
- [ ] Real scoring role against Groq `gpt-oss-120b` on a gold case returns a valid
      JSON verdict at acceptable latency, using the live key.
- [ ] Real caveman compressor against Cerebras `gpt-oss-120b` preserves required
      symbols and compresses, using the live key.
- [ ] The per-handle fallback chain actually fires when the primary errors / rate
      limits (simulate a failure) and lands on the next free quota before spend.
- [ ] `ctx=work` / `is_corporate_context` never reaches a hosted provider (the
      compressor falls back to the original text; scoring respects the D3 gate).
- [ ] Measured latency + any free-tier limit hit recorded in the PR/notes.

**Files.** `src/axon/router/llm_backend.py`, `src/axon/expansion/scoring.py`,
`src/axon/router/compressor.py` (read-only verification; fix only if a gap is
found). Optionally a live smoke test under `tests/` skipped without the keys.

**Test plan.** A live smoke test gated on `GROQ_API_KEY` / `CEREBRAS_API_KEY`
presence (skip otherwise), exercising both roles + the fallback + the `ctx=work`
block. Kept out of the default CI run (needs network + keys) via a marker.

---

Epic: **bge-m3 embedder + local→cloud fallback** -
[`docs/superpowers/specs/2026-07-02-embedder-bge-m3-fallback-design.md`](superpowers/specs/2026-07-02-embedder-bge-m3-fallback-design.md)
(issue #45). Root cause: code chunks embed raw source via `bge-small-en` and lose
NL queries to doc chunks; fix = swap to `bge-m3` (dim 1024) served by a configurable
Ollama→NIM→DeepInfra chain (all the exact same model, verified vector-identical).
Recommended order: **EMB-4** (independent quick win) → **EMB-1** → **EMB-2** →
**EMB-3**; EMB-5 is operational (not FORGE).

---

## EMB-1 - Widen the loop gate to cover the embedder + a retrieval-eval smoke

- Priority: P2 | Size: S | Status: ready | Depends-on: green candidate baseline
- Specs: bge-m3-fallback design, "Gate"; benchmark-guided evolution, "Task EMB-1"
- Note: reactivated as the existing hermetic gate item; do not create a second
  harness, golden set, or retrieval-eval implementation.

**Problem.** `.claude/loop.yaml` `gate_cmd` runs only `router/resilience/store/scripts`.
It does NOT run `tests/embedder` or `tests/benchmark`, so a FORGE slice that changes
the embedder would pass the gate WITHOUT running its own tests. The embedder epic
must not land through a gate that can't see it.

**Acceptance criteria.**
- [ ] `gate_cmd` in `.claude/loop.yaml` additionally runs `tests/embedder` and a fast
      `retrieval_eval` unit smoke (the injected-fakes test, `tests/benchmark/test_retrieval_eval.py`),
      NOT the live-DB sweep.
- [ ] The added suites are green on `master` before widening (if any are red from
      pre-existing debt, fix or explicitly scope them out in a comment — do not
      widen onto red).
- [ ] Comment updated to state what the gate now covers and why.
- [ ] Each added test group has a non-zero collected count and runs without
      `.env`, credentials, network, Postgres, or GPU.
- [ ] The live recall guard remains opt-in and outside the merge gate.

**Files.** `.claude/loop.yaml`.

**Test plan.** Run the new `gate_cmd` locally against `master`; confirm green and that
it actually executes the embedder + eval tests (non-zero collected count).

---

## EMB-2 - bge-m3 embedder with configurable Ollama→NIM→DeepInfra provider chain

- Priority: P1 | Size: M | Status: done (PR #47) | Depends-on: EMB-1
- Spec: bge-m3-fallback design, "Components" + "provider chain"

**Problem.** `EmbedderEngine` (`src/axon/embedder/engine.py`) is fastembed/onnx
in-process (`bge-small-en`, dim 384). We need it to embed via **bge-m3** through an
ordered, configurable chain of providers that all serve the exact `BAAI/bge-m3`
model, so query and chunk vectors stay numerically interchangeable and the embedder
survives a local Ollama outage.

**Acceptance criteria.**
- [ ] `EmbedderEngine.embed()` / `embed_one()` keep their signatures; all existing
      callers compile unchanged.
- [ ] Providers tried in order: Ollama (`AXON_OLLAMA_LOCAL_HOST` `/api/embed`,
      model `bge-m3`) → NIM (`/v1/embeddings`, `baai/bge-m3`, `NVIDIA_NIM_API_KEY`) →
      DeepInfra (OpenAI-compatible `/v1/openai/embeddings`, `BAAI/bge-m3`,
      `DEEPINFRA_API_KEY`). Order + membership come from config, not hardcoded.
- [ ] On a provider error/timeout, fall through to the next; if all fail, raise a
      clear error (never return a wrong-dim/empty vector silently).
- [ ] Returned vectors are L2-normalized; `vector_dim()` returns 1024 for bge-m3.
- [ ] Provider onboarding check helper: embed a fixed sample via the local provider
      and a candidate, assert cosine ≥ 0.999 (guards the normalization/float caveat).
- [ ] Prefer `litellm.embedding()` for the OpenAI-compatible providers + Ollama if it
      supports them cleanly; otherwise thin HTTP adapters behind one interface. Record
      which was used and why.
- [ ] TDD with fakes: fake providers (no network) prove ordering, fallthrough, the
      all-fail error, and normalization. No live calls in unit tests.

**Files.** `src/axon/embedder/engine.py` (+ a new provider module if adapters are
hand-rolled), `src/axon/config/runtime.py` (config for the chain), `tests/embedder/`.

**Test plan.** Unit: injected fake providers for order/fallthrough/all-fail/normalize.
Optional gated integration test (skipped without keys) hitting Ollama+NIM asserting
cosine ≥ 0.999 between them.

---

## EMB-3 - Migrate vector dim 384->1024 and make bge-m3 the default

- Priority: P1 | Size: M | Status: done (PR #47) | Depends-on: EMB-2
- Spec: bge-m3-fallback design, "table migration"

**Problem.** bge-m3 is dim 1024 vs the current 384. The `embeddings.vector` and
`recall_embeddings.vector` columns and the dim config must move to 1024; dims cannot
be mixed, so this is a hard cutover (the actual data re-index is the operational
EMB-5, but the schema + default + guards land here).

**Acceptance criteria.**
- [ ] Vector columns declared/created at dim 1024 (`pg_vector_store` schema +
      `FASTEMBED_MODEL_DIMS`/`vector_dim()` updated for bge-m3).
- [ ] The default embedder model is bge-m3 via the EMB-2 chain (no code path still
      defaults to `bge-small-en`).
- [ ] A guard/migration ensures a 384-dim legacy table is detected and refuses mixed
      dims with a clear message pointing at the re-index (EMB-5).
- [ ] `retrieval_eval` over the grounded golden set shows code recall@k strictly
      higher than the bge-small-en baseline on a re-embedded sample (the gate for this
      change). Record the before/after numbers in the PR.

**Files.** `src/axon/store/pg_vector_store.py`, `src/axon/embedder/engine.py`,
`src/axon/benchmark/retrieval_eval.py` (if a before/after harness helper is needed),
`tests/embedder/`, `tests/store/`.

**Test plan.** Unit: dim config = 1024; mixed-dim guard raises. Gated: re-embed the
golden symbols + queries with bge-m3 and assert recall@k improves vs the recorded
bge-small-en baseline.

---

## EMB-4 - Fix empty retrieval for valid queries (query-side filter)

- Priority: P1 | Size: S | Status: done (PR #47) | Depends-on: none
- Spec: bge-m3-fallback design, "secondary bug"

**Problem.** Two golden queries return an EMPTY retrieval from `_retrieve_context`
despite the expected symbol existing in the index at cosine ~0.51:
`"onde um arquivo e dividido em pedacos e gravado no banco de vetores"` (`ingest_file`,
ctx=personal) and `"de onde a ferramenta descobre a versao mais recente publicada pra
baixar"` (`resolve_latest_tag`, ctx=personal). A query-side filter (strategy /
collections / language / a score threshold) is dropping all results. Independent of
the embedder swap.

**Acceptance criteria.**
- [ ] Root cause of the empty result identified (which filter/branch in the
      `_retrieve_context` → `_select_retrieval_strategy` → `pg_vector_store.search`
      path zeroes the hits) and stated in the PR.
- [ ] The two repro queries return non-empty hits containing plausible symbols.
- [ ] Regression test at the correct seam reproduces "valid query -> empty" before the
      fix and passes after. If no correct seam exists, that is the finding — document it.
- [ ] No broadening of scope: fix only the drop-everything path, not ranking.

**Files.** `src/axon/mcp/server.py` (`_retrieve_context` / strategy select),
`src/axon/store/pg_vector_store.py` (if a threshold/filter lives there), `tests/`.

**Test plan.** Reproduce via a gated live-DB test with the two queries, or a unit test
at the filter seam with a fixture that mimics the offending strategy/collection config.

---

## EMB-5 - Operational: re-index the corpus with bge-m3 (dim 1024)

- Priority: P1 | Size: M | Status: done (applied 2026-07-02) | Depends-on: EMB-3
- Spec: bge-m3-fallback design | **Operational - NOT a FORGE code slice**
- Result: prod re-indexed to dim 1024 (12189 chunks); code recall@10 0.333 → 0.875 on the
  golden set; self-correction bands validated unchanged (LOW=0.30/HIGH=0.85).

**Problem.** After EMB-3 lands the 1024-dim schema + bge-m3 default, the existing
384-dim corpus (code AND docs, all ctx) must be re-embedded. This needs live pgvector
(`AXON_PG_URL`) + a live embedding provider (Ollama/NIM/DeepInfra) and is run by the
operator, like the dec-121 backfill - not by FORGE.

**Acceptance criteria (operator runbook).**
- [ ] Re-index every ctx (personal/knowledge/saas; career is ~empty) via the standard
      index path with the bge-m3 chain active.
- [ ] Post-re-index, all vectors are dim 1024 and row counts are within expected range
      of the prior corpus (no silent drops).
- [ ] Run the `retrieval_eval` live sweep over the golden set; record code recall@k
      before (bge-small-en) vs after (bge-m3) as the acceptance evidence.
- [ ] Re-calibrate `LOW`/`HIGH` for the self-correction loop against the new corpus
      (PR #44's wide 0.30/0.85 was set on the old, docs-dominated bge-small-en index).

**Files.** operational (index path invocation + `scripts/calibrate_retrieval_bands.py`);
no source change expected beyond recorded constants.

**Test plan.** The live `retrieval_eval` sweep IS the test: code recall@k must rise
materially vs the recorded bge-small-en baseline.
