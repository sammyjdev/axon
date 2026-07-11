## Validation: issue #71 — PASS

Tier: Legendary (risk_area_hit=true on `secrets` — HARD RULE forces Legendary
regardless of the underlying complexity, which the classifier independently
rated Common: "clear, checkable CI job addition"). Flow: Plan (Opus 4.8) ->
Fetch (Haiku 4.5) -> Execute (Sonnet 5) -> Review (Sonnet 5 spec + Opus 4.8
code-quality), per `tiers/legendary.md`.

Spec-anchored check: 3/3 ACs matched (no `spec.md` for this issue — entered
via `task` directly, not `blueprint` — so verification is via direct command
execution + diff inspection, per the same non-code exemption issues #67 and
#70 used on this branch: no application Python source was touched, only
`.github/workflows/ci.yml` and `SECURITY.md`, so there is no pytest test file
to map criteria to).

- AC1 (new job in `ci.yml`, runs on every PR, scans the PR's commit range —
  not just the diff): verified by diff inspection — a new `secret-scan` job
  (`gitleaks/gitleaks-action@v3`) sits alongside the existing `lint`/
  `compile`/`test`/`profile-smoke`/`audit` jobs with no job-level `if:`
  override, so it inherits the file-level `on: pull_request` / `push:
  [master, main]` triggers. `fetch-depth: 0` is present on the checkout step
  (mandatory — the action's internal `pull_request` handling walks
  `baseRef^..headRef` across every commit in the PR, which a shallow clone
  cannot do). The action's actual source (`gitleaks/gitleaks-action` tag
  `v3.0.0`, `src/gitleaks.js`/`src/index.js`, fetched and read directly) was
  used to confirm this behavior rather than assumed from its README: on
  `pull_request` events it sets `baseRef`/`headRef` from the PR's full commit
  list (not the squash-merge diff) and runs `gitleaks detect
  --log-opts="--no-merges --first-parent {baseRef}^..{headRef}"` — i.e. it
  walks every commit in the PR range. This was independently demonstrated
  (not just cited) via a fixture: see AC3 below, and Mutation 2.
- AC2 (job is green on current `master` history — no pre-existing leaked
  secrets): verified by running the pinned gitleaks CLI (v8.30.1, matching
  `.pre-commit-config.yaml`'s `rev: v8.30.1` — the CLI is not installed
  system-wide on this machine, `which gitleaks` returns nothing, so the exact
  pinned binary was downloaded from the `v8.30.1` GitHub release
  (`gitleaks_8.30.1_darwin_arm64.tar.gz`) into scratch) against the full
  reachable history of `master`:
  `gitleaks detect --source . --log-opts="master" --redact -v` ->
  **"479 commits scanned... no leaks found", exit 0.** Run twice
  independently (once by the orchestrator before this pass's commits, once
  by the Execute subagent after) with an identical result — this pass's
  commits land on `agent/benchmark-claude-security`, not `master`, so
  neither run could have been affected by the diff either way. No leak was
  found, so the "STOP and report, do not silently allowlist" branch of the
  AC was never triggered.
- AC3 (validated once during implementation with a fixture commit containing
  an obviously-fake secret pattern, then that fixture commit removed before
  merge): a `ghp_`-shaped (GitHub PAT rule) synthetic token
  (`ghp_$(openssl rand -hex 18)`, never reused anywhere, redacted from all
  reports) was committed to an obviously-scratch file
  (`FIXTURE_LEAK_DO_NOT_MERGE.txt`), then removed in a second commit. Two
  gitleaks CLI invocations were run against the resulting two-commit range,
  mirroring the shipped job's exact mechanism:
  - PR-range scan (mirrors `gitleaks-action`'s `pull_request` handling —
    `--log-opts="--no-merges --first-parent {A}^..{B}"`): **caught it**
    (rule `github-pat`, exit 1) — proves the shipped job would catch a
    secret added-then-removed within a PR.
  - Final-tree-only scan (`--no-git`, i.e. "just the diff" / final state):
    **missed it** (exit 0) — the file no longer exists in the tree, so a
    diff-only check finds nothing. This is the concrete, executed proof of
    AC1's "not just the diff" requirement, not merely an assertion.
  Cleanup: `git reset --hard` restored HEAD to the pre-fixture commit exactly
  (`git rev-parse HEAD` matched the saved `$START`), `git log --oneline`
  shows no TEMP fixture commits, the fixture file is absent, `git status
  --porcelain` is empty. Performed independently twice — once by the Execute
  subagent as part of implementation, once by the orchestrator as part of
  Quench Mutation 2 (below) — both with identical results, and the branch
  was never pushed at any point (`git status -sb` shows no upstream ref).
  **Residual gap, stated explicitly per this pass's constraints:** no `git
  push` / PR is created in this pass, so the shipped job has not been
  verified running inside a real GitHub Actions execution (only the pinned
  CLI, invoked locally with the exact command construction the action
  performs, which is the closest achievable equivalent). This should be
  confirmed once the real PR is opened and CI actually runs.

Mutation sensor (Legendary tier: 5+ required — 6 injected, 6 killed, 0
survived; scratch state only, real worktree files never left mutated):
1. Dropped `fetch-depth: 0` — cloned the worktree with `git clone --depth 1`
   into scratch, then attempted the real range command
   (`git log ca81f0f^..5fdb43c`) inside the shallow clone. Result: `fatal:
   ambiguous argument... unknown revision`, exit 128 — killed. Proves
   `fetch-depth: 0` is load-bearing, not decorative. Scratch clone deleted
   after.
2. PR-range walk vs final-tree-only scan (independent re-verification of
   AC3/AC1, run separately from Execute's own run, same method): fresh
   fixture commit pair, range scan exit 1 (caught), final-tree scan exit 0
   (missed) — killed (in the sense that mutating "range scan" down to
   "diff-only scan" loses the finding). Cleaned up via `git reset --hard`,
   verified HEAD restored and tree clean.
3. `GITLEAKS_VERSION` cross-layer drift: asserted `"8.30.1"` appears in
   `ci.yml` AND `v8.30.1` appears in `.pre-commit-config.yaml` (both true
   pre-mutation). Mutated a scratch copy of `ci.yml` (`sed
   s/8.30.1/8.24.3/`) — the two-file consistency assertion then fails
   (`ci_pinned=False`) — killed. Scratch copy discarded, real file untouched.
4. `GITHUB_TOKEN` omitted (source-evidenced — not independently runnable
   without a real GitHub Actions context): `gitleaks-action`'s own
   `src/index.js` (`ScanPullRequest`) does `if (!process.env.GITHUB_TOKEN) {
   core.error(...); process.exit(1); }`. Confirmed the shipped job's `env:`
   block contains `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` (`grep`
   match). Removing that line would hit this documented hard-exit on every
   PR run — killed by source inspection, flagged as the one sensor of the
   six that could not be executed end-to-end.
5. Dropped the whole `secret-scan` job block (structural): `yaml.safe_load`
   on the real `ci.yml` confirms `'secret-scan' in d['jobs']` is `True`;
   deleting the key in-memory (`del d['jobs']['secret-scan']`) flips the
   assertion to `False` — killed. Real file never touched (in-memory dict
   only).
6. Injected an `if: github.event_name == 'push'` into the job (AC1
   regression — would silently exclude PRs): confirmed the real job has no
   `if:` key (`'if' not in j` is `True`); injecting one in-memory flips it to
   `False` — killed. Real file never touched.

Pre-check 0 (test-file structural diff): `git diff 7fb7d32...5fdb43c --
tests/ test/` for this pass's two commits (`ca81f0f`, `5fdb43c`) is empty —
neither commit touches any test file.

Independent review: 2 Legendary-tier reviewers.
- Spec reviewer (Sonnet 5): APPROVE. Confirmed AC1-AC3, scope (`.pre-commit-
  config.yaml` untouched, other 5 jobs untouched), RULES.md invariants (no
  secret values in the diff, plain hyphens), non-code TDD exemption valid by
  precedent (#67/#70).
- Code-quality reviewer (Opus 4.8), with risk scrutiny for the `secrets`
  risk area: APPROVE. Confirmed `fetch-depth: 0`, `GITHUB_TOKEN`, least-
  privilege `permissions:` (`contents: read` + `pull-requests: read`, no
  `write` needed since `GITLEAKS_ENABLE_COMMENTS: "false"`), the
  `GITLEAKS_VERSION` cross-layer pin rationale, no silent-no-op risk, no
  secret values leaked, `SECURITY.md` accuracy/style. Three non-blocking
  LOW/judgment notes: `pull-requests: read` may be one scope broader than
  strictly required (harmless, defensive); the SECURITY.md bullet says
  "every pull request" but the job also runs on push to master/main
  (understates, safe direction); `@v3` is a floating tag rather than a SHA
  pin, but matches this file's existing convention for every other action —
  a lone SHA pin here would be an inconsistency, not an improvement, absent
  a repo-wide policy.

No blocking findings from either reviewer — 0 review rounds needed.

Anneal: nothing to append to RULES.md "Proposed by the loop" this pass — no
surviving mutant, no spec-precision gap, no failed AC, and reviewers raised
only non-blocking observations.

Gate (`.claude/loop.yaml` `gate_cmd`): `ruff check src/axon/router
src/axon/resilience tests/router tests/resilience` clean (no findings);
`pytest tests/router tests/resilience tests/store tests/scripts tests/cli
tests/doctor -q` -> 273 passed, 6 failed, 7 skipped. The 6 failures
(`tests/store/test_save_code_change_unification.py`,
`tests/store/test_session_store_find_by_hash.py` x3,
`tests/cli/test_pb_cli.py::test_graph_neighbors_lists_edges`,
`::test_graph_path_prints_route`) are the same pre-existing shared-Postgres-
contention failures documented for issues #67/#68 — confirmed non-regression
by checking out this pass's base commit (`7fb7d32`, pre-#71) and re-running
the same failing test paths: identical 6 failures, identical test names,
present without any of this pass's changes applied.

Gate-coverage note: `gate_cmd` runs `ruff`/`pytest` only — it does not lint
or otherwise exercise `.github/workflows/ci.yml` or `SECURITY.md` (no YAML
linter or GitHub-Actions-specific check is part of this repo's gate). This is
inherent to any CI-workflow-only issue (same as #67/#70) and was covered
instead by direct command execution (gitleaks CLI runs, `yaml.safe_load`
structural checks) rather than the pytest-based gate.

Report: .specs/features/71-gitleaks-full-history-ci-job/validation.md
