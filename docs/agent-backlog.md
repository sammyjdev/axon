# Agent Backlog

Candidate work items for the agentic builder loop (`agent-task` / `agent-run`).
Each item is independently pickable: scope, acceptance criteria, and test plan
are self-contained. Shared context lives in the linked spec.

Status legend: `ready` (pickable now) | `blocked` (waiting on a dep) |
`in-progress` | `done`. Priority: P1 (correctness/high-impact) > P2 (safety) >
P3 (quality). Size: S (<1d) / M (1-2d) / L (multi-day).

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
