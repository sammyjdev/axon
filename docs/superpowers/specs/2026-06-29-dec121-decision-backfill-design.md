# dec-121 Decision/ADR Backfill — Design

**Status:** approved (brainstorming), awaiting implementation plan.
**Relation:** unblocks sub-project B Task 4 (vault re-export + re-index), which is a
separate step run *after* this lands.

## Goal

Backfill the legacy SQLite decisions (110) and ADRs (33) into the active Postgres
store, resolving the decision-id collision, so `axon export`, decision recall, and
`get_adrs` see the full history instead of only the few rows captured directly to PG.

## Background (diagnosed)

The dec-121 SQLite→Postgres migration is half-on: `AXON_PG_URL` is set, so
`SessionStore` reads/writes the **Postgres** backend, but the legacy data was never
copied. Concretely (as of 2026-06-29):

- SQLite `data/axon.db`: `decisions`=110 (ids `dec-001..dec-110`, contiguous),
  `adr`=33.
- Postgres (port 5434): `decisions`=5 (`dec-001..dec-005`, genuinely new — recent
  local-roles-era commits, git hashes not present in SQLite), `adr`=0.
- **ID collision:** ids `dec-001..dec-005` exist in BOTH stores with different
  content. The PG counter restarted at `dec-001` on the empty PG table.
- `next_decision_id` was already fixed (commit `886ee6b`) to `max(numeric id)+1` per
  backend, so it composes with this backfill (continues at `dec-116` afterward).

Both `decisions` tables share an identical schema: `id TEXT PK, frontmatter
(jsonb/text), body TEXT, vault_path TEXT, created_at TEXT`. Decision ids are
referenced by `linked_decisions` wikilinks, vault note filenames, and ~207 embedding
symbols — so the 110 legacy ids must be **preserved**, and the 5 PG-native ones (new,
largely unreferenced) are the ones that get renumbered.

## Scope

**In scope**
- A new idempotent CLI command that copies `decisions` + `adr` from SQLite into
  Postgres, resolving the decision-id collision.
- Tests (testcontainers Postgres + a temp SQLite fixture reproducing the collision).

**Out of scope**
- Vault re-export + re-index (that is sub-project B Task 4, run after this).
- The other stranded tables (`nodes`, `edges`, `code_change`, `commits`,
  `session_memory`, `file_index`, …) — the full dec-121 migration is a separate,
  larger effort.
- Retiring SQLite. The backfill is additive; SQLite is left untouched as a backup.

## Collision-resolution strategy (chosen)

**SQLite is authoritative for the legacy id namespace; PG-native collisions are
renumbered to continue after the legacy max.**
- Rejected B (PG authoritative, renumber the 110 SQLite): renumbers referenced
  decisions — huge blast radius.
- Rejected C (natural-key merge, no renumber): impossible — id is the PK in
  `dec-NNN` format, the numeric collision is unavoidable.

## Components

### Command surface

`pb migrate decisions-sqlite-to-pg` (a `migrate` sub-app in `cli/pb.py`).
- `--dry-run`: print the plan (rows to copy, PG-native rows to renumber and their new
  ids, rows skipped as duplicates) and write nothing.
- `--sqlite <path>` (default `_RUNTIME.db_path` / `data/axon.db`) and the PG dsn from
  `AXON_PG_URL` / `_RUNTIME.pg_url`.
- Idempotent: safe to re-run; a second run is a no-op.

The migration logic lives in a testable module (e.g.
`src/axon/store/decision_backfill.py`) that the CLI command is a thin wrapper over —
so it can be unit-tested without the CLI.

### ADR copy (the easy half)

PG `adr`=0, so there is no collision. Copy all 33 SQLite ADRs into PG via the existing
idempotent path (`PostgresDecisionRepository.save_adr` already has
`ON CONFLICT (project, title, created_at) DO NOTHING`). A re-run skips already-present
ADRs by that natural key.

### Decision copy + collision resolution (the meat)

1. Load all SQLite decisions and all PG decisions.
2. Build the set of SQLite `git_hash` values (non-empty).
3. **Resolve PG-native rows:** for each PG decision whose `id` collides with a SQLite
   id OR whose content is not from SQLite:
   - If its `git_hash` is non-empty and matches a SQLite decision → it is a duplicate
     of a legacy decision → drop the PG row (SQLite is authoritative).
   - Else → it is PG-native/new → reassign it a fresh id continuing after
     `max(numeric id across BOTH stores)` (e.g. `dec-111`, `dec-112`, …), preserving
     its content; update the PG row's `id` (and its frontmatter `id` field).
   - Empty-`git_hash` edge: a PG-native row with an empty git_hash and a colliding id
     is treated as native (renumber) unless its full content equals the SQLite row at
     that id (then it is a duplicate → drop).
4. **Copy legacy:** insert all 110 SQLite decisions into PG with their original ids,
   `ON CONFLICT (id) DO NOTHING` (after step 3, no live collision remains; the
   conflict-guard makes re-runs no-ops).

The 5 current PG-native rows (`dec-001..005`) have git hashes absent from SQLite, so
they all renumber to `dec-111..dec-115`; the 110 legacy ids copy in unchanged.

### Idempotency

A re-run observes: legacy ids already present in PG (skip via `ON CONFLICT`); PG-native
rows already carrying ids `> legacy max` (already renumbered → leave alone); ADRs
already present (skip via natural key). Net effect of a second run: zero writes.

## Data flow

SQLite `data/axon.db` (read-only) → `decision_backfill` module: resolve renumbering of
PG-native rows in PG → copy legacy decisions + ADRs into PG → report a summary
(`copied`, `renumbered: dec-00X→dec-11Y`, `skipped_dup`). `--dry-run` stops before any
write and prints the same report.

## Error handling / safety

- Read-only on SQLite; all writes target Postgres and are additive (insert + id
  reassign). No deletes against SQLite.
- `--dry-run` first-class so the user previews the exact renumbering before committing.
- Wrap the PG writes so a mid-run failure is re-runnable (idempotency covers partial
  application); ideally the decision phase runs in a transaction.
- Renumbering updates BOTH the row `id` column and the `id` inside the `frontmatter`
  JSON so the two never drift.

## Testing (TDD)

Use the existing `testcontainers` Postgres fixture pattern (see
`tests/store/test_pg_decision_repository.py`) plus a temp SQLite file built with the
real schema:

- **Collision repro:** SQLite has `dec-001..dec-003` (distinct git hashes); PG has
  `dec-001..dec-002` with *different* git hashes (PG-native). After migration: PG has
  `dec-001..dec-003` = the SQLite content (original ids) **plus** the 2 PG-native rows
  renumbered to `dec-004`/`dec-005`, content preserved. Assert ids, contents, and that
  the renumbered rows' `frontmatter.id` matches their new id.
- **Duplicate skip:** a PG row whose `git_hash` matches a SQLite row is dropped, not
  renumbered.
- **Idempotency:** running the migration twice yields the same PG state (second run
  writes nothing — assert via row count + a write counter or before/after snapshot).
- **ADR copy:** 33 SQLite ADRs land in PG; re-run does not duplicate them.
- **Dry-run:** `--dry-run` reports the plan and leaves PG unchanged.
- **Empty-git_hash edge:** a SQLite legacy row with empty git_hash copies under its own
  id without being treated as a duplicate.

## Out of scope / follow-ups

- Sub-project B Task 4: re-export `AXON/*` from the now-complete PG decisions + re-index
  (verify `dec-NNN > {summary}` symbols, no doubled id, frontmatter not embedded).
- Full dec-121 migration of the remaining stranded tables.
- Optional later: a guard/doctor check that warns when `AXON_PG_URL` is set but the PG
  decisions count is far below SQLite (the condition that produced this bug).
