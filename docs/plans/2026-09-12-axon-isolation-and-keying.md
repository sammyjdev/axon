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
- [ ] **Task 2: add a purge script for the fixture rows and the test-written handoff briefs**
- [ ] **Task 3: key sessions by repo_identity on write**
- [ ] **Task 4: key session_memory by repo_identity**
- [ ] **Task 5: derive embeddings.project from the repo root**

### Task 1: isolate the vault in the handoff test and extend the live-write guard to file paths

> **DONE 2026-09-12** in `839ed6d`, by hand rather than through the loop. Arming the
> guard surfaced two further leaks of the same shape, both fixed in the same commit:
> `_COMPRESSION_TELEMETRY` (server.py:82) and the separate `_RUNTIME` that
> `axon.cli.pb` binds at import (pb.py:50). Ledger: `.forge/sdd/progress.md`.


`tests/mcp/test_axon_tools.py::test_axon_handoff_includes_context` takes only the
`store` fixture. `tests/mcp/test_handoff_persists.py:25-29` already has the correct
`vault` fixture that monkeypatches `server.discover_vault` to a `tmp_path`; reuse it
rather than writing a second one. Then widen
`tests/test_suite_never_writes_to_the_live_store.py` so it fails on file writes, not
only on the operator's DSN - a session-scoped autouse check that snapshots the real
`data_root` and `AXON_VAULT` and asserts nothing under them changed.

  - Depends on: none
  - Acceptance: a full suite run leaves `~/vault/knowledge/handoffs/` and
    `data/compression/stats.jsonl` byte-identical; deleting the isolation fixture from
    the handoff test makes the guard fail, not merely the handoff test

### Task 2: add a purge script for the fixture rows and the test-written handoff briefs

A one-off cleanup with a dry run by default, because it touches the live store and the
operator's vault repo. Fixture summaries to match: `a decision`, `first decision`,
`add redis cache`, `drop neo4j backend`, `adopt sqlite graph`. Test-written briefs are
the ones with no `## From this session` section - the real ones always carry caller
notes.

  - Depends on: Task 1
  - Acceptance: `scripts/purge_test_artifacts.py` with no flags lists the 6 fixture rows
    and the 120 briefs and changes nothing; with `--apply` a query for those summaries
    returns 0 and every remaining brief has a `## From this session` section

### Task 3: key sessions by repo_identity on write

`axon_session_start` takes whatever `repo` the caller passes and `_detect_repo()`
returns a path on rails that are not Claude Code. Normalize on write through the same
`repo_identity()` the decisions path uses, and re-key the 5 existing rows that hold
absolute paths.

  - Depends on: Task 1
  - Acceptance: `axon_session_start` invoked with cwd `/Users/samdev/dev/axon` stores
    `repo='axon'` and its returned recall is non-empty; no row in `sessions` has a
    `repo` value starting with `/`

### Task 4: key session_memory by repo_identity

Issue #186. `session_memory` is keyed by `basename(cwd)`, so a session run from a
worktree or a scratch directory files under that directory's name instead of the
repo's.

  - Depends on: Task 3
  - Acceptance: a session saved from a worktree under `~/dev/axon-worktrees/` keys to
    `axon`, and `get_session_memory('axon')` returns it

### Task 5: derive embeddings.project from the repo root

`project` currently holds a directory-name fragment, so it is not a project
identifier at all. The table already stores `file_path`, so existing rows can be
re-keyed with an UPDATE that derives the root - **no re-embedding of the 24,164
vectors is required**. Change the derivation at write time and backfill in the same
task.

  - Depends on: Task 3
  - Acceptance: no `project` value maps to more than one repo root; a `search_code`
    scoped to one project returns no chunk whose `file_path` lies outside it
