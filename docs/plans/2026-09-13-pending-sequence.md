# Pending work after PR #204 - execution sequence

## Goal

Four plan documents sit under `docs/plans/`. One is closed on paper and unapplied on
disk, one is the fix for why it cannot be applied, one is that fix's input, and one is
a large independent build that would inherit the defect if it started first. This
document orders them and names the two operator decisions the loop must not make.

It adds no tasks of its own. Every task below points at the plan that already defines
it, with its acceptance criteria. Read this to know what runs next; read the linked
plan to run it.

## State on 2026-09-13

| Plan | Tasks | State |
|---|---:|---|
| `2026-09-12-axon-isolation-and-keying.md` | 6/6 | merged as `f41ea4d` (PR #204); its three scripts are dry-run-by-default and **none has been applied** |
| `2026-09-13-dead-dirs-key-analysis.md` | n/a | analysis, complete; input to the next row |
| `2026-09-13-finish-the-keying-flow.md` | 0/4 phases | replaces `rekey-fallback-and-outcome-purge` (3 tasks, deleted). Phases A and D need no code; B is the only task left and blocks the embeddings apply |
| `2026-09-13-activity-history-llmops.md` | 0/11 | not started; independent build, AGY-executed |

Outside the plans: PR #179 open since July; issues #202, #200, #196, #187 touch the
spool and hooks and are not on this path.

## Why this order

The isolation plan's Task 5 acceptance does not hold on real data: the resolver falls
back to the directory basename when the directory is gone, and 7,324 of 24,164
`embeddings` rows are silent no-ops under it. Applying the scripts as merged would write
the exact collapse the plan set out to remove (`627 rows -> project='tests'`). So the
rekey-fallback plan runs first, the applies run second, and only then does anything that
stores a repo key start.

The activity-history plan's Task 8 links existing AXON records by explicit provenance
keyed on repository. Started before the applies, it links against broken keys and has
to be re-keyed too. It waits.

## Operator decisions (not the loop's)

Both are already argued in the analysis document; they are listed here so the pass does
not stall on them.

- [ ] **D1: `~/vault/work-notes` (6 rows).** Indexed as `knowledge` (2) and `personal` (4)
  under a directory named `work`. Either audit and confirm the rows are not restricted
  content, or delete them, before any apply runs. The analysis recommends delete-or-audit,
  never re-key.
- [ ] **D2: confirm the refusals.** `dev/gnomon-eval-src` (31 rows) is refused, not aliased
  to `gnomon-eval`; `~/vault/**` gets no repo key. The rekey plan already settles both.
  The decision here is only to ratify, so the loop does not re-open them.

## Tasks

- [ ] **Task 1: run the rekey-fallback plan**
- [ ] **Task 2: apply the four re-key scripts against the live store**
- [ ] **Task 3: run the activity-history plan**

### Task 1: run the rekey-fallback plan

> **Updated 2026-09-13, after PR #204 merged.** The referenced plan was replaced by
> `2026-09-13-finish-the-keying-flow.md` and shrank from three tasks to one. Its other two
> built an `outcome_record` target for the purge script and an `--outcomes` scope for the
> re-key, both written while the leak that produced those rows was open. `ff547a1` closed the
> write path and a regression test pins it, so 22 rows of one shape and 1 of another remain
> in a table nothing writes badly any more: a `DELETE` finishes that, and a script feature
> would exist for a leak already closed. The same reasoning retired the `sessions` and
> `session_note` re-key in favour of deleting 7 valueless rows, which is what this document's
> own rekey caveat recommends.
>
> Consequence for the order below: the purges and deletions moved into that plan's **Phase A**,
> which needs no code and does not depend on Task 1. Only the `embeddings` backfill still
> waits on the resolver. D1 still gates Phase A; its measurement is now recorded there
> (6 chunks, one file, `ctx` personal and knowledge, no `ctx='work'` anywhere).

`forge plan docs/plans/2026-09-13-finish-the-keying-flow.md`. One task: a path-deriving
resolver for `embeddings` that refuses rather than guesses. It doubles as the codex-migration
pilot, which wants a small forge task driven from inside Codex.

  - Depends on: D1, D2, and the Codex hooks being installed
  - Acceptance: the plan's own, per task. The one that gates Task 2 here: over the
    216-directory fixture every directory is keyed or refused, none basename-guessed, and
    a refused row is byte-identical after `--apply` and named in the report.

### Task 2: apply the four re-key scripts against the live store

Operator step, outside the loop, as decided 2026-09-12. Order: purge first (the
isolation plan's purge script, then the outcome_record purge), then re-key `sessions`,
`session_note`, `embeddings.project`, `outcome_record`. Dry-run each, read the refused
list, then `--apply`.

  - Depends on: Task 1 merged
  - Acceptance:
    - every dry run reports changed + refused = total rows; no silent no-op bucket
    - after apply, no `project` value equals a bare basename such as `tests` or `src`
    - `products/merit-worktrees/agent-issue-6` rows read `merit`
    - the refused rows are unchanged and listed in the run's report, kept with the run

### Task 3: run the activity-history plan

`forge plan docs/plans/2026-09-13-activity-history-llmops.md`, AGY rail as the document
specifies. Eleven tasks in four slices, each slice independently releasable through its
gate.

  - Depends on: Task 2 applied. Tasks 0-7 of that plan store no repo key and could start
    earlier in principle; they do not, because a half-applied store is exactly the state
    the previous plan's reviewers missed.
  - Acceptance: the plan's own, per slice.

## Out of scope

- PR #179 and the open spool/hook issues (#202, #200, #196, #187). Separate passes.
- Re-keying `recall_embeddings` (self-contained eval corpus) and giving `~/vault/**` a
  repo key. Both settled in the rekey plan.
- Any `--apply` run by the loop. Applies are the operator's.
