# Finish the keying flow

Supersedes `2026-09-13-rekey-fallback-and-outcome-purge.md`, which had three tasks. Two of
them are no longer worth building; see "What changed". The measurements and the dead-directory
classification it cites stand, and the classification itself lives in
`2026-09-13-dead-dirs-key-analysis.md`.

## Done means

No key in the live store names something that is not a repository, and nothing the test suite
wrote is still in the operator's data. Measured, not asserted.

## What changed

The earlier plan proposed extending `purge_test_artifacts.py` with an `outcome_record` target
and `rekey_sessions.py` with an `--outcomes` scope. Both were written while the leak that
produced those rows was still open. `ff547a1` bound `server._outcome_store` to the isolated
DSN and `test_the_outcome_store_follows_the_active_dsn` pins it, so no new fixture row can
appear. What remains is 22 rows of one shape and 1 row of another, in a table nothing writes
badly any more. A `DELETE` finishes that; a script feature would exist for a leak already
closed.

The same reasoning applies to `sessions` and `session_note`: 7 rows whose directories no
longer exist, of no value, where deleting is cleaner than re-keying wrong. That was decided
independently in the codex-migration session on 2026-09-13.

`embeddings` is the only place where deletion is not an option, because the rows are real
vectors. It is therefore the only place that still needs code.

## The dependency

Phase A depends on nothing. Phase B depends on the Codex hooks being installed, which depends
on `agent/plan-codex-axon-hooks` (2 commits, claude-skills) being published: the installer
lives on that unpushed branch. That is the single real blocker in the chain, and it is work
that already exists. Phase C depends on Phase B. Phase D depends on nothing.

claude-skills PR #57 is independent of all of it.

## Phase A: everything that needs no new code

Run each dry run, read it, then apply. The `--apply` runs are the operator's, outside any
loop, as decided 2026-09-12.

### A0: settle the work-notes rows first

`2026-09-13-pending-sequence.md` raises this as operator decision D1 and gates every apply on
it. Measured 2026-09-13, and narrower than that document assumed: **6 chunks, all from one
file**, `~/vault/work-notes/2026-07-16-definicao-do-roadmap-e-entregas.md`, indexed as
`ctx=personal` (4) and `ctx=knowledge` (2). No row anywhere in `embeddings` carries
`ctx='work'`.

If that file is work content it is reachable today by `search_code` and `ask` without the
explicit `ctx="work"` the restricted-context rule requires. The file was not opened while
measuring this. Audit it or delete the rows; never re-key them into a repository.

  - Acceptance: either the 6 rows are gone, or the audit is recorded and they are confirmed
    non-restricted.

### A1: purge the fixture decisions and the test-written briefs

```
python3 scripts/purge_test_artifacts.py                    # dry run, already reviewed
python3 scripts/purge_test_artifacts.py --apply --all
```

Selects 6 decision rows and 120 briefs. The brief selector requires a recalled-decision line
(`- dec-001 (rank 0.40): a decision`), not containment anywhere in the file, after a
third-family review found the old selector would delete a real note-less brief whose prose
used the words. Verified against the real vault: 122 briefs present, 120 selected, all exactly
218 bytes, both real briefs preserved.

  - Reversible: the briefs live in a git repo inside the vault; the decisions do not. Take a
    `pg_dump` of `decisions` first.
  - Acceptance: a re-run of the dry run reports 0 decisions and 0 briefs, and
    `~/vault/knowledge/handoffs/` holds only briefs carrying a `## From this session` section
    or no fixture citation.

### A2: delete the 7 rows whose directories no longer exist

`sessions` 5 rows, `session_note` 2. Two of them would re-key to `agent-issue-6` and to a
benchmark arm name, neither of which is a repository. Deleting is cleaner than writing a key
that is wrong in a different way.

  - Acceptance: no row in `sessions` or `session_note` has a `repo`/`project` starting with
    `/`, and the rows that remain all name a real repository.

### A3: delete the 22 fixture rows in outcome_record

`project='outcome_target'`, `summary='shipped feature'`, written by
`tests/mcp/test_repo_resolution.py` while `_outcome_store` was bound to the import-time DSN.
The count moved from 21 to 22 within one day before the fix landed; re-count at apply time
rather than trusting a number written here.

The other 44 rows are real outcomes from actual passes (`axon`, `gnomon-eval`,
`claude-skills`) and must not be touched.

  - Acceptance: a query for that project/summary pair returns 0, and `SELECT count(*)` over
    the table returns 44 plus whatever real outcomes landed since.

### A4: correct the one outcome_record row keyed `samdev`

`samdev` is the home directory's basename, which is what the old keying produced for a path
outside any repository. The row is a real outcome (`axon #185: doctor's capture-path checks`),
so it is corrected, not deleted.

  - Acceptance: `UPDATE outcome_record SET project='axon' WHERE project='samdev'` affects
    exactly 1 row, and no row in the table holds a non-repository name.

**After Phase A the only keying defect left in the live store is `embeddings`.**

## Phase B: the one piece of code left, and the Codex pilot

One resolver, used by `rekey_embeddings_project.py`, in three ordered steps:

1. Directory exists: `repo_identity()`, as today.
2. Directory gone: derive from the **text** of the path. No filesystem, no git. Rules from
   `2026-09-13-dead-dirs-key-analysis.md`:
   - repos that moved from `dev/<X>` to `dev/products/<X>` or `dev/tools/<X>` keep `<X>`
   - case and separator aliases: `Pharos`→`pharos`, `PitStopOS`→`pitstop-os`,
     `Orion-AI`→`orion-ai`, `linkedin_content_manager`→`linkedin-content-manager`,
     `piloto_revvo`→`revvo`
   - `<repo>-worktrees/<branch>` keys to `<repo>`
   - a nested repo declared in ROUTER keys to itself, not to its parent
   - segment matching is **exact**, never by prefix: prefix makes `claude-usage-bar` swallow
     `claude-usage-bar-rs` and `pharos` swallow `pharos-backend`
3. Neither applies: **refuse**. The row is left exactly as it is and named in the report.

Refusal covers, by explicit denylist rather than heuristic: `~/vault/**` and the scratch roots
`_bench`, `_worktrees`, `_wt`. A benchmark arm under `_bench` has a `.git` and a `src/`, so it
is textually indistinguishable from a real repo; only a denylist separates them.
`dev/_wt/<branch>` is unresolvable by construction. The single ambiguous case,
`dev/gnomon-eval-src` (31 rows), is refused rather than prefix-matched.

`rekey_sessions.py` keeps its current resolver: after A2 it has no dead-directory rows left to
mis-key.

**This task is also the migration's pilot.** The codex-migration handoff wants a small
`forge task` driven from inside Codex, crossing worktree → red test → gate → quench → PR, as
the first real exercise of an envelope that reports `in sync` and has never run. This is that
task: small, fully specified, with a real fixture and a measured acceptance criterion. Better
than inventing a throwaway.

  - Depends on: Codex hooks installed, which depends on `agent/plan-codex-axon-hooks` being
    published
  - Acceptance:
    - over the real 216-directory fixture, every directory is keyed or refused, and none is
      basename-guessed
    - no two distinct repository roots resolve to the same key, asserted over that fixture
    - a path under `_bench`, `_wt`, `_worktrees` or `~/vault` is refused, and
      `gnomon-eval-src` is refused
    - the **7,324 rows that are silent no-ops today** are either changed or reported as
      refused; a row that cannot be keyed never counts as coverage
    - a refused row is byte-identical after `--apply` and appears in the report

## Phase C: the backfill

```
python3 scripts/rekey_embeddings_project.py                 # dry run
python3 scripts/rekey_embeddings_project.py --apply --all
```

Only after Phase B merges. Today's decomposition of the 24,164 rows, re-measured against
merged `master`:

| rows | directory | outcome today |
|---:|---|---|
| 16,386 | exists | correct, via git |
| 7,324 | gone | silent no-op, not even counted as changed |
| 454 | gone | wrong basename written over a different wrong value |

  - Reversible: `pg_dump` the `project` column keyed by `id` before applying.
  - Acceptance: no `project` value maps to more than one repository root, checked over the
    whole table and not a sample; every row not changed is explicitly reported as refused.

## Phase D: housekeeping

- 18 of the 20 axon worktrees hold branches already merged to `master`.
- Four stashes: two in claude-skills (one removes 1015 lines from
  `design-taste-frontend/SKILL.md`), two in axon (one from this session holding a superseded
  edit of the isolation plan, one `wip(oss-branch)` from 2026-06-25).
- `fix/maker-timeout-and-aging-fixture` still holds `b05b5f9`, the maker-ceiling fix, unpushed.
- axon PR #179, open since 2026-08-31, unrelated to this flow.

## Out of scope

- `recall_embeddings`: all 1,117 rows carry `project='recall'` and repo-relative paths,
  uniformly. A self-contained eval corpus with its own convention, not a keying defect.
- `~/vault/**` content in the index: 1,113 rows from `vault/AXON/Decisions`, split across two
  `ctx` values. What is missing is a field distinguishing repo-sourced from vault-sourced
  content, not a better repository key. Separate concern.
- The drift guard is not affected by any of this: `check_onboarding_drift.py` compares repos
  carrying the AXON post-commit hook against `~/.claude/axon/ROUTER.md` and never reads
  `embeddings.project`.
