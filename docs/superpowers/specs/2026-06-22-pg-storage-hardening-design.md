# Design: Postgres storage hardening (dec-121 follow-ups)

- Date: 2026-06-22
- Status: proposed (post-merge review + market benchmark)
- Implements: hardening of dec-121 step 3 (relational source of truth on Postgres)
- Builds on: file_index (w1) + graph (w2) + decisions (w3) + sessions (w4), merged to `master` @ be41082
- Branch: `spec/pg-storage-hardening` (spec only; each mini-spec implements on its own branch)

## Why

The dec-121 Postgres migration shipped behind a per-concern `SessionRepository`
/ `GraphRepository` / `DecisionRepository` pattern selected by
`AXON_<CONCERN>_BACKEND > AXON_DB_BACKEND`. A post-merge code review plus a
market benchmark (top async-Python repos, Postgres/asyncpg docs) confirmed the
**execution is faithful and the pattern is legitimate** (it mirrors
`chaosblade-io/chaosblade`, 6.3k stars, near line-for-line), but surfaced a set
of correctness and best-practice gaps in the **underlying pattern** (inherited
by every wave, since waves 3-4 deliberately mirrored wave 2 as "behavior
preserving").

This design captures those findings and slices them into **independent,
agent-pickable mini-specs** so they can be prioritized and worked in parallel.
The mini-specs themselves live in `docs/agent-backlog.md`; this document is the
shared context (findings, rationale, dependency graph) they all reference.

## Findings (review + market)

| # | Finding | Severity | Ours or inherited | Mini-spec |
| --- | --- | --- | --- | --- |
| F1 | Timestamps stored as `text` (ISO), not `timestamptz`; `ORDER BY created_at` only correct while every value is uniform-UTC | HIGH | inherited | MS-1 |
| F2 | `migrate_*` copy not idempotent for memory/note (plain INSERT, no conflict target) despite "idempotent" docstring | HIGH | ours | MS-2 |
| F3 | `end_session` is a non-atomic SELECT+UPDATE on Postgres (no txn); `save_session` ON CONFLICT resets `started_at`/`ended_at` on re-save | HIGH | inherited (ports SQLite `INSERT OR REPLACE`) | MS-3 |
| F4 | Postgres schema via inline `CREATE TABLE IF NOT EXISTS`; no `schema_version` parity with the SQLite path; silent drift on shape change | MEDIUM | inherited | MS-4 |
| F5 | Lazy pool/`_sessions()` init not coroutine-safe (double-init race; orphaned pool) | MEDIUM | inherited (also `_graph`/`_decisions`) | MS-5 |
| F6 | `save_code_change` catches `aiosqlite.OperationalError` on the Postgres path (dead code); SQLite-only pending fallback + duplicated helpers | MEDIUM | inherited | MS-6 |
| F7 | Migration validated by row COUNT only; SQLite->PG boundary silently coerces types | MEDIUM | ours | MS-7 |
| F8 | `Repository` Protocol methods partly untyped (`mem`/`note`/`change`); two hand-written SQL impls drift risk (paramstyle, upsert, types) | LOW | inherited | MS-8 |

Market verdict: keep the Protocol-per-concern pattern (validated by chaosblade),
keep the lazy asyncpg pool + acquire-per-call (idiomatic for a CLI/MCP process),
keep the layered precedence; the recurring critique is **SQL drift between the
two hand-maintained implementations** and **type fidelity** (timestamps,
migration validation). The dominant alternative (SQLAlchemy dialect abstraction,
single `DATABASE_URL`) is explicitly NOT adopted - it would couple the clean
domain to an ORM and is out of scope (see Non-goals).

## Scope

In scope: the eight mini-specs (MS-1..MS-8) below, each hardening the relational
Postgres path. Primary target is the **sessions** wave (the most recently
shipped and the review's focus); where a finding is repo-wide (F1/F4/F5/F8) the
mini-spec ships the sessions fix and flags the same change for graph/decisions/
file_index as an explicit follow-up rather than boiling the ocean in one unit.

Non-goals:

- Replacing the hand-written repositories with SQLAlchemy/an ORM (rejected:
  couples the domain to a heavy dep; the whole dec-121 design is ORM-free on
  purpose). MS-8 reduces drift WITHIN the hand-written approach instead.
- Removing the per-concern flags or the `AXON_DB_BACKEND` master switch.
- Re-opening the SQLite-as-rollback guarantee (every mini-spec must keep
  `AXON_<CONCERN>_BACKEND=sqlite` / `AXON_DB_BACKEND=sqlite` working).

## Slicing + dependency graph

```
P1 (correctness / data fidelity, high impact, cheap):
  MS-2  copy idempotency + honest docstring            (independent)
  MS-3  end_session atomicity + save_session semantics (independent; behavior decision)
  MS-1  timestamps text -> timestamptz                 (soft-needs MS-4)

P2 (safety / foundation):
  MS-4  Postgres schema_version + migration runner     (enables MS-1 clean ALTER)
  MS-5  coroutine-safe lazy pool / repo init           (independent)

P3 (hardening / quality):
  MS-6  unify save_code_change error handling + dedupe helpers (independent)
  MS-7  migration validation beyond row counts (checksums)      (soft-synergy MS-1)
  MS-8  type the Protocol + shared column/helper module         (independent)
```

Edges (soft = "cleaner if done first", not a hard block):

- MS-1 -> MS-4 (soft): altering `text` -> `timestamptz` on a live table wants a
  versioned migration; without MS-4, MS-1 ships a guarded one-off DDL.
- MS-7 -> MS-1 (soft): content checksums need correct per-type normalization;
  fixing timestamp types first makes the checksum simpler.

Everything else is parallelizable. Recommended first wave for agents: MS-2, MS-3,
MS-5 (all independent, no soft edges), then MS-4 -> MS-1, then MS-7/MS-6/MS-8.

## Cross-cutting constraints (apply to every mini-spec)

- TDD: a failing test first; the existing session/store/config tests are the
  regression guard. Postgres behavior is covered by `testcontainers[postgres]`.
- Keep the 9 session-method signatures + return shapes unchanged unless the
  mini-spec explicitly changes a contract (only MS-3 and MS-8 touch signatures).
- Keep SQLite a one-flag rollback; do not weaken the conftest backend pins.
- Plain hyphens only. `rtk pytest` / `rtk ruff check` before done.

## Success criteria

1. Each mini-spec is independently mergeable, has its own acceptance criteria +
   test plan, and leaves the suite green (minus the known env-leak failures).
2. After MS-1+MS-4: session timestamps are `timestamptz`, ordering is correct by
   type, and a versioned migration tracks the change.
3. After MS-2+MS-7: re-running any `migrate_*` script is provably idempotent and
   validated by content (not just counts).
4. After MS-3+MS-5: no non-atomic session mutation, no destructive re-save of
   `started_at`, no double-init pool race.
5. The Protocol-per-concern pattern and SQLite rollback are preserved throughout.
