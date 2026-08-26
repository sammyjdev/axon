# Spec review - issue #158 (stable repo identity for worktrees)

Verdict: APPROVE

Reviewer: independent spec reviewer (Legendary tier). Read-only over the worktree
diff (`git diff master` - the branch has no commits yet, all work is in the
working tree).

## Structural check (pre-semantic)

Three existing test files are touched: `tests/hooks/test_file_bridge.py`,
`tests/hooks/test_git_event.py`, `tests/hooks/test_git_event_post_merge.py`.
The diff is **+118 / -0** across all three - purely appended fixtures and new
test functions. No existing test body, assertion, fixture or import was altered
or removed. That is the permitted RED phase, not an existing-test modification,
so this is not a structural BLOCK.

## Criteria coverage

| Done-when criterion | Asserting test | Verdict |
|---|---|---|
| Real worktree of a temp repo, fire the commit hook inside it, assert `Decision.repo` == parent key | `tests/hooks/test_git_event.py::test_on_commit_from_a_worktree_files_under_the_parent_repo_key` - creates a real `git worktree add`, commits, calls `on_commit`, asserts `repo == "myrepo"` **and** `find_decisions_by_repo("agent-issue-158") == []` | met |
| `on_push` from a worktree counts the parent repo's decisions for `detect_scope_end` | `tests/hooks/test_git_event.py::test_on_push_from_a_worktree_counts_the_parent_repos_decisions` - 10 decisions under `myrepo` (threshold is `DEFAULT_DECISION_THRESHOLD = 10`), push fired from the worktree, asserts the parent-keyed architecture doc and journal entry were exported and the worktree-keyed doc was not | met |
| One-off backfill / documented manual step re-keys `agent-issue-*` rows onto `axon`, no deletion | `axon rekey-repo <checkout> [--apply]` in `src/axon/__main__.py`, covered by `tests/cli/test_rekey_repo_cli.py` (dry run by default, apply moves only rows whose `git_hash` is present in the checkout, row count unchanged). Operator steps documented in `docs/decisions/dec-129-stable-repo-identity.md` | met |
| `judged` stays canonical; `validation_score == 0.0` is not a sentinel | `test_apply_preserves_judged_and_validation_score` asserts a re-keyed row keeps `judged=True` with `validation_score=0.0`. The re-key path round-trips the full Pydantic model through the id-keyed upsert, so no field is reconstructed | met |

Additional coverage beyond the letter of the criteria but inside the issue's root
cause (the same path value used as an identity at 12 sites): `tests/core/test_repo_identity.py`
(main checkout, worktree, subdirectory of both, unrelated repos do not collide,
non-git fallback), `tests/mcp/test_repo_detection.py` (`_detect_repo`), and the
file-bridge and post-merge worktree tests. All are real `git init` / `git worktree add`
repos and a real Postgres (`tests/conftest.py` `_isolate_axon_engine` points every
test at an isolated container and truncates between tests) - no fake-only coverage.

## Fix direction

The issue proposed `--git-common-dir` or the origin slug. The diff takes
`--git-common-dir` and `docs/decisions/dec-129-stable-repo-identity.md` records
why the remote slug was rejected (it would re-key correct rows, breaks for
remote-less repos, and still collides on basename). Resolution is centralised in
one 24-line helper, `src/axon/core/repo_identity.py`, and every identity site was
converted: `grep -rE "root\.name|cwd\(\)\.name|repo_root\.name" src/` now hits
only the fallback branches inside that helper. `_repo_root` stays a path helper -
git operations still take the real checkout path - which is the correct split.

## Scope

Nothing extra. `SessionStore.all_decisions` is a four-line passthrough the
backfill needs; `PostgresDecisionRepository.all_decisions` already existed. The
`__main__` command is the deliverable backfill. The `mcp/server.py` change is the
twelfth identity site, not drive-by refactoring.

## RULES.md invariants (risk-area scrutiny)

- dec-121 `judged` canonical: honored, and asserted by a test.
- dec-105 Pydantic v2: the re-key uses `Decision.model_validate({**model_dump(), ...})`,
  not an ad-hoc dict write.
- `SessionStore` explicit `.init()`: present on every store opened by the new command
  and its tests, each with a `finally: await store.close()`.
- No vault/restricted content, no credentials, no consent gate touched.
- Backfill never deletes: `save_decision` is `ON CONFLICT (id) DO UPDATE`, and the
  test asserts the row count is unchanged.

## Evidence

- Full gate: `python -m pytest -q` -> **1765 passed, 7 skipped, 7 xfailed** in 226s.
- Targeted: the six affected/new test files -> 43 passed.
- `ruff check` on every touched file -> clean (the repo-wide 128 S1xx/S6xx findings
  are pre-existing and outside the configured `gate_cmd` paths).

## Non-blocking observations (for the record, not fix requests)

1. `on_push` calls `find_decisions_by_repo(repo)` with the default `limit=20`, so
   `decisions_since_export` saturates at 20 and `_judge_and_export` scores at most
   20 rows per push. The 51 backlogged rows will therefore clear over three pushes
   rather than one. Pre-existing behaviour, unchanged by this diff.
2. `_reachable` uses `git cat-file -e <hash>^{commit}`, which tests object presence
   rather than ref reachability. That is the desired looser check (rebased-away
   worktree commits still re-key), and dec-129 explicitly documents the residual
   "two repos sharing a hash - last migration wins" case.
3. `repo_identity` swallows a bare `Exception` to fall back to the basename. This
   matches the existing `_detect_repo_root` idiom in `mcp/server.py` and is covered
   by the non-git-directory test.
