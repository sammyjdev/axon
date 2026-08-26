# Code-quality re-review (round 2) - issue #158 @ 24463ac

Verdict: APPROVE

## What I verified

**Finding 1 - existing test files modified: FIXED.**
`git diff master...HEAD --stat -- tests/` lists exactly four paths, and
`git cat-file -e master:<path>` fails for all four, i.e. none existed on master:
tests/hooks/test_worktree_identity.py, tests/core/test_repo_identity.py,
tests/cli/test_rekey_repo_cli.py, tests/mcp/test_repo_detection.py.
No pre-existing test file appears in the diff. Structural gate passes.

**Finding 2 - rekey-repo could overwrite a legitimate identity: FIXED.**
`src/axon/__main__.py::rekey_repo` now refuses (`typer.Exit(2)`, nothing written)
when `--apply` is passed without `--only-key`/`--all`, and the per-row filter is
`not all and only_key and not any(fnmatch...)` -> with `--only-key` alone, a row
whose `repo` does not match any glob is skipped before any write. `--all` is an
explicit opt-out of the filter, which is the documented contract. Reachability
(`git cat-file -e <hash>^{commit}`) is still a mandatory precondition and is
evaluated for every candidate. Rows are never deleted: the only write is
`store.save_decision(Decision.model_validate({**decision.model_dump(), "repo": target}))`,
an upsert by id that carries `judged`/`validation_score` through unchanged - and
`test_apply_preserves_judged_and_validation_score` asserts exactly that, so the
RULES.md/dec-121 canonical-flag invariant is honored. The guard is covered by
`test_apply_without_a_key_filter_refuses`,
`test_only_key_preserves_a_legitimately_keyed_row` (two rows on one real commit;
`glyph-kg` preserved) and `test_all_moves_every_provable_row`.

**Finding 3 - empty diff: FIXED.** `master...HEAD` is commit 24463ac,
12 files, +686/-18.

## Correctness of the current diff

- `src/axon/core/repo_identity.py` resolves `--git-common-dir` relative to the
  probed path (correct for both the relative `.git` and the absolute
  linked-worktree form), strips a bare `*.git`, and falls back to the basename on
  any failure - so a non-git directory keeps master's behavior rather than raising
  inside a hook.
- Root-cause coverage, not symptom coverage: `grep -rn "root\.name\|cwd()\.name"
  src/` now returns hits only inside `repo_identity.py`. Every prior caller
  (`on_commit`, `on_push`, `_scan_pulled_range`, `file_bridge.update_context_file`,
  `mcp/server.py::_detect_repo`/`axon_session_end`, CLI `status`/`export`) routes
  through the shared helper. `_judge_and_export`'s new `(store, decisions, repo)`
  signature has no other callers (grep confirms).
- The new tests assert observable behavior (a real `git worktree add` of a real
  temp repo; the decision lands under `myrepo`, the vault doc is `myrepo.md`, and
  `find_decisions_by_repo("agent-issue-158")` is empty). They would fail against
  master's `root.name`.

## Executed here

`python -m pytest tests/hooks/test_worktree_identity.py tests/core/test_repo_identity.py
tests/cli/test_rekey_repo_cli.py tests/mcp/test_repo_detection.py -q` -> **18 passed**,
no Postgres access needed. I did NOT re-run the full 1716-test suite or the
mutation sensor; I relied on the reported evidence for those.

## Non-blocking nits (do not fix for this round)

- The `--all` option shadows the `all` builtin inside `rekey_repo`; harmless today
  (only `any()` is used in the body) but a trap for the next editor.
- The dry run prints one line per row *and* the grouped summary; on the real ~350-row
  store the summary scrolls off. Consider gating the per-row list behind `-v`.
