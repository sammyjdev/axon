# AXON isolation and keying

## Goal

AXON exists to carry context between models and agents. Two measured defects stop
it from doing that, and one of them also makes every measurement of the first
untrustworthy.

**Test runs write into the operator's live environment.** `tests/mcp/test_axon_tools.py`
calls `axon_handoff` with the `store` fixture but no vault isolation, so
`discover_vault()` resolves the real `AXON_VAULT`. 120 of the 121 files in
`~/vault/knowledge/handoffs/` are 218-byte test briefs, all committed and pushed
hourly to a private repo. The same class writes to `data/compression/stats.jsonl`
(issue #203) and left 6 fixture rows in the decision store. The existing guard,
`tests/test_suite_never_writes_to_the_live_store.py`, covers `AXON_PG_URL` only -
never a filesystem path.

**Three of four repo keys are wrong.** `decisions` uses `repo_identity()` (dec-129)
and is correct: 23 keys by name, 0 absolute paths. The other three are not:

| table | key today | measured consequence |
|---|---|---|
| `sessions` | whatever the rail sends | non-Claude rails write absolute paths; recall returned empty on 5 of 5 codex/luna-2 sessions, including one in the axon repo where 398 decisions were waiting |
| `session_memory` | `basename(cwd)` | worktree and scratch sessions key to the wrong repo (issue #186) |
| `embeddings.project` | a directory-name fragment | 117 of 233 "projects" mix more than one real repo; `project='tests'` holds 4,225 chunks from 15 repos |

The correct key function already exists in the codebase. Three of the four tables
do not use it.

## Out of scope

Removing dead surface (`symbol_deps`, the two uncalled graph MCP tools, `pet`,
`benchmark`, `expansion`, the `rtk` CLI wrappers) is a separate plan. `nodes` and
`edges` stay: `search_code` and `ask` read them live.

## Tasks

- [x] **Task 1: isolate the vault in the handoff test and extend the live-write guard to file paths**
- [x] **Task 0: make the live-write guard attribute writes to the test process** (blocks the rest)
- [x] **Task 2: add a purge script for the fixture rows and the test-written handoff briefs**
- [x] **Task 3: key sessions by repo_identity on write**
- [x] **Task 4: key session_memory by repo_identity** - already landed in `084d326` / PR #194
- [x] **Task 5: derive embeddings.project from the repo root**

All tasks landed in PR #204. The three scripts are dry-run by default and the loop never
ran `--apply`: purging the live store and vault, and re-keying the `sessions`,
`session_memory` and `embeddings` rows, remain the operator's own run.

> **Execution rule for tasks 2, 3 and 5, decided 2026-09-13.** The loop delivers code, a
> dry-run-by-default script and tests against an ephemeral Postgres. It never runs
> `--apply` against the live store or the operator's vault; that run is the operator's,
> outside the loop. The precedent is `axon rekey-repo` (`src/axon/__main__.py:392`): dry
> run by default, `--apply` refused without an explicit scope flag.

### Task 0: make the live-write guard attribute writes to the test process

The guard Task 1 installed (`tests/conftest.py:71`,
`_the_suite_never_writes_to_the_operators_files`) fingerprints paths that belong to the
**machine**, not to the pytest process. `@traced_tool` appends to
`$AXON_ENGINE/data/trace/records.jsonl` on every MCP call, and the AXON git hooks write
there on every commit. Any agent loop that calls recall / search_code / record_outcome
around its gates therefore fails the gate and blames an innocent fixture. Measured
2026-09-13: exit 1 on the first baseline run naming `data/trace/records.jsonl`, then
2085 passed / exit 0 on a rerun with no concurrent MCP traffic, the file byte-identical
either way. Until this lands, every gate run in this repo is a coin flip.

  - Depends on: none; blocks 2, 3 and 5
  - Acceptance: a full suite run stays green while another process appends to
    `data/trace/records.jsonl` mid-run, and deleting the vault fixture from
    `tests/mcp/test_axon_tools.py::test_axon_handoff_includes_context` still makes the
    guard fail

### Task 2: add a purge script for the fixture rows and the test-written handoff briefs

A one-off cleanup with a dry run by default, because it touches the live store and the
operator's vault repo. Fixture summaries to match: `a decision`, `first decision`,
`add redis cache`, `drop neo4j backend`, `adopt sqlite graph`. Test-written briefs are
selected conjunctively: no `## From this session` section AND their recalled context
cites at least one of those fixture summaries. A real note-less brief that does not
cite a fixture summary is preserved.

  - Depends on: Task 0
  - Acceptance: `scripts/purge_test_artifacts.py` with no flags lists the matching rows
    and briefs and changes nothing; `--apply` is refused without an explicit scope flag;
    against an ephemeral Postgres and a temp vault seeded with those fixture summaries
    and briefs, `--apply` leaves 0 matching rows and every remaining brief has a
    `## From this session` section; a real note-less brief not citing fixture summaries
    survives. Running it against the operator's live store and vault is the operator's,
    outside the loop.

### Task 3: key sessions by repo_identity on write

`_detect_repo()` is already correct. What passes raw is the `repo` the **caller** sends,
and that happens at **ten** call sites in `src/axon/mcp/server.py`, not eight:
`axon_session_start` (1132), `axon_capture_event` (1172), `axon_record_outcome` (1197),
`axon_get_context` (1289), `axon_capture` (1305), `axon_search` (1327),
`axon_handoff` (1359), `axon_export_now` (1407), `axon_mark_done` (1416), and
`axon_validation_stats` (1431). The read paths matter as much as the writes: a search
with a raw absolute path finds nothing, and `axon_validation_stats` had no normalization
or default at all. `axon_export_now` is destructive and names vault documents after
`repo`. One resolver, ten one-line call sites.

  - Depends on: Task 0
  - Acceptance: `axon_session_start` invoked with cwd `/Users/samdev/dev/axon` stores
    `repo='axon'` and its returned recall is non-empty; a caller passing an absolute path
    at any of the ten sites is normalized to the repo name; a re-key script for the
    rows already holding absolute paths exists, dry-run by default. Applying it to the
    live store is the operator's run.

### Task 4: key session_memory by repo_identity

> **ALREADY DONE** in `084d326` / PR #194, merged 2026-09-11, one day before this plan
> was written. `session_save` and `compact_hook` use `repo_identity(cwd)`
> (`src/axon/cli/pb.py:1303` and `:1586`), and the acceptance test already exists and
> passes:
> `tests/cli/test_session_memory_repo_key.py::test_session_save_in_a_worktree_keys_under_the_parent_repo`.
> Re-keying rows already written with the old key is not covered by this task's
> acceptance; it rides with the re-key work in Task 3.

### Task 5: derive embeddings.project from the repo root

`project` currently holds a directory-name fragment, so it is not a project
identifier at all. The table already stores `file_path`, so existing rows can be
re-keyed with an UPDATE that derives the root - **no re-embedding of the 24,164
vectors is required**. Change the derivation at write time and write the backfill in the
same task.

  - Depends on: Task 3
  - Acceptance: new writes derive `project` from the repo root; the backfill exists as a
    dry-run-by-default script, proven against an ephemeral Postgres seeded with rows from
    the mixed projects (including a `project='tests'` row set spanning more than one
    repo), after which no `project` maps to more than one repo root and a `search_code`
    scoped to one project returns no chunk whose `file_path` lies outside it. Applying it
    to the 24,164 live rows is the operator's run.
