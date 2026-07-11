## Validation: issue #67 — PASS

Spec-anchored check: 3/3 ACs matched (no `spec.md` for this issue — entered via
`task` directly, not `blueprint` — so verification is via direct command
execution + diff inspection, per the Plan stage's stated non-code exemption
for a declarative-config-only issue).

- AC1 (`pre-commit run --all-files` passes clean on current master): verified
  by running the command itself — both `ruff-check` and `gitleaks` hooks
  report `Passed`.
- AC2 (SECURITY.md / contributor docs updated): verified by diff inspection —
  `SECURITY.md` gains a `pre-commit hooks` bullet, `CONTRIBUTING.md` gains a
  `pre-commit install` step; the pre-existing `axon hooks install` bullet in
  SECURITY.md is untouched (see trap note below).
- AC3 (old installer removed if fully superseded): resolved to "do not
  remove" — `axon hooks install` (`src/axon/hooks/git_installer.py`, dec-113)
  is a distinct mechanism (ADR-capture git hooks, not lint/secret-scanning)
  with live dependents (`tests/hooks/test_hooks_cli.py`,
  `scripts/check_onboarding_drift.py`). Diff confirms zero changes under
  `src/axon/hooks/`.

Mutation sensor: 5 injected, 5 killed, 0 survived.
1. Stripped the `exclude:` regex from `.pre-commit-config.yaml` → `ruff-check`
   hook failed (was passing before) — killed.
2. Reverted the `src/axon/doctor/__init__.py` import-order fix → `ruff check`
   on that file failed again (I001) — killed.
3. Corrupted the YAML syntax (unterminated flow sequence) → `yaml.safe_load`
   raised `ParserError` — killed. (Note: `pre-commit validate-config`'s own
   exit code was unreliable in this shell's command-rewrite wrapper for this
   specific check; the real acceptance-criterion command, `pre-commit run`,
   was independently confirmed correct via mutations 1 and 4.)
4. Staged a fake Slack-bot-token-shaped secret in a scratch file → `gitleaks`
   hook failed and reported the finding (`RuleID: slack-bot-token`) — killed.
   (First attempt used the well-known `AKIAIOSFODNN7EXAMPLE` AWS placeholder,
   which is allowlisted by gitleaks as a documented example — survived, as
   expected for an intentionally-benign string; retried with a token shape
   that isn't allowlisted, which killed correctly.)
5. Pointed the `ruff-pre-commit` repo `rev:` at a nonexistent tag → `pre-commit
   run` failed with `pathspec ... did not match any file(s) known to git` —
   killed.

All mutations were applied in-place to the real worktree files and reverted
immediately after each check (verified via `diff` against a pre-mutation
backup after every revert); no mutation was left in the tree.

Gate (`.claude/loop.yaml` `gate_cmd`): ruff clean; pytest 273 passed, 6
failed, 7 skipped. The 6 failures (`tests/store/test_save_code_change_unification.py`,
`tests/store/test_session_store_find_by_hash.py`,
`tests/cli/test_pb_cli.py::test_graph_neighbors_lists_edges`,
`::test_graph_path_prints_route`) are pre-existing/base-state — confirmed
identical with this issue's diff `git stash`-ed, i.e. present without any of
this issue's changes applied. Not a regression introduced by #67.

Report: .specs/features/67-pre-commit-config-ruff-gitleaks/validation.md
