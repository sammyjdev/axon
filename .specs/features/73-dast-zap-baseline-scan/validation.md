## Validation: issue #73 - PASS

Tier: Common (classifier: Haiku 4.5, `risk_area_hit=false`, complexity=Common
- "clear, checkable CI-workflow-only job addition, no application source
touched, matches the shape of already-merged #70/#71"; posture=internal +
complexity=Common => Common per the posture-floor matrix; no contradiction
with the HARD RULE, so no override was needed). Flow: Maker (Haiku 4.5) ->
Reviewer (Sonnet 5, spec-compliance only), per `tiers/common.md`, with model
routing per this pass's benchmark-mode deviation (Common maker = Haiku 4.5,
reviewer = Sonnet 5 rather than tiers/common.md's default Haiku 4.5 reviewer).

Spec-anchored check: 3/3 ACs matched (no `spec.md` for this issue - entered
via `task` directly, not `blueprint` - so verification is via direct command
execution + diff inspection, per the same non-code exemption issues #67/#70/
#71 used on this branch: no application Python source was touched, only
`.github/workflows/ci.yml`, `SECURITY.md`, and `CONTRIBUTING.md`, so there is
no pytest test file to map criteria to).

- AC1 (new job in `ci.yml`, runs on every PR, exits 0 regardless of findings,
  always uploads the report artifact): a new `dast` job sits alongside the
  existing `lint`/`compile`/`test`/`profile-smoke`/`audit`/`secret-scan`
  jobs. `python3 -c "import yaml; ..."` confirms `'dast' in jobs` and that the
  job has no job-level `if:`/`on:` override, so it inherits the file-level
  `on: {pull_request, push: [master, main]}` trigger. Non-blocking is
  belt-and-suspenders: `fail_action: false` on the `zaproxy/action-baseline`
  step (its documented default, set explicitly) means ZAP alerts (exit code
  1/2 from the underlying docker script) never fail the action, and
  `continue-on-error: true` on the step additionally absorbs any scan-level
  infrastructure failure (docker pull hiccup, exit code 3). This was
  confirmed by fetching and reading the action's actual `dist/index.js`
  (v0.15.0) rather than trusting its README: it runs `zap-baseline.py -t
  <target> -J report_json.json -w report_md.md -r report_html.html` inside
  `docker run --network="host" ...`, then unconditionally calls
  `processReport(...)` (which performs the artifact upload) UNLESS the
  docker run itself exits with code 3 (target totally unreachable) - that one
  path short-circuits via `core.setFailed` before the upload. The
  readiness-wait step (AC2) is what keeps that path from being hit in
  practice; see the mutation sensor below, which demonstrates it is
  load-bearing for exactly this reason.
- AC2 (job reliably starts and tears down the server; no orphaned process):
  the job boots `axon`'s FastAPI server via `python3 -m uvicorn
  axon.http.app:app --host 127.0.0.1 --port 8765` in the background, polls
  `curl -sf http://127.0.0.1:8765/health` up to 30x1s before invoking the
  scan, and an `if: always()` cleanup step kills the captured PID. Verified
  independently (not just by reading the YAML): booted the exact same command
  with `AXON_PG_URL`/`AXON_ENGINE`/`AXON_VAULT` all unset (simulating a bare
  CI runner, no external services) - `/health`, `/openapi.json`, `/docs`,
  `/api/gain`, `/api/activity`, `/dashboard` all returned 200; `POST
  /v1/chat/completions` returned 500 fast (no hang) with no backend
  reachable, which is fine, it is not a target endpoint the baseline scan
  needs working. The teardown mechanism (`echo $! > pidfile`, later `kill
  "$(cat pidfile)"`) was independently re-run: PID captured correctly, `kill
  -0` confirmed alive before / dead after, and the port became unreachable
  immediately after - no orphan.
- AC3 (README/CONTRIBUTING note on the report artifact location): no CI
  section exists in `README.md` (confirmed by grep - the file never mentions
  `ci.yml`/GitHub Actions at all), so touching it would have been an
  unrequested addition inconsistent with how #70/#71 handled the identical
  either/or wording (both touched only `SECURITY.md`). `CONTRIBUTING.md`
  already documents the CI pipeline; it gained a new "CI workflow and
  artifacts" section naming the artifact (`dast-zap-baseline-report`)
  verbatim matching the YAML's `artifact_name` input, plus a matching
  `SECURITY.md` Scope bullet (mirroring the pattern #71 used for its own
  gitleaks CI note, commit 5fdb43c).

Mutation sensor (Common tier: 1 required - 1 injected, 1 killed, 0 survived;
scratch state only, real worktree files never left mutated):
1. Dropped the readiness-wait step (the "required side effect" being the
   health-check gate before the scan target is trusted reachable): started
   the server the same way the job does, then hit `/health` with `--max-time
   1` at literally zero delay after backgrounding, with no poll loop.
   Result: `curl` exit 7 (connection refused), `http_code=000` - killed.
   Re-checked after a 3s wait (what the real readiness loop provides):
   `http_code=200`. This demonstrates the readiness-wait step is load-bearing
   for AC1's "always uploads the report artifact" guarantee (per the
   `dist/index.js` reading above, an unreachable target is the one case that
   skips the artifact upload entirely) and for AC2. Scratch server processes
   killed immediately after each check; no worktree file was touched by this
   sensor (it only mutated the *procedure*, not any file).

Pre-check 0 (test-file structural diff): `git diff 3e91d08...HEAD -- tests/
test/` is empty - neither commit touches any test file.

Independent review: 1 Common-tier reviewer (Sonnet 5, spec-compliance only,
per this pass's benchmark model routing).
- Round 1: BLOCKING. `CONTRIBUTING.md`'s new "CI workflow and artifacts"
  section introduced one em-dash (a dash character where "use it to review..."
  followed), confirmed new to this diff (absent from the pre-existing file) -
  a RULES.md "Plain hyphens only" violation. The other four em-dash hits the
  reviewer found via `grep -n "—\|–"` across the three touched files were all
  pre-existing (not introduced by this pass) and correctly left untouched per
  the surgical-changes rule.
- Fix round 1/2: maker (Haiku 4.5) amended the docs commit in place (unpushed
  branch, so amend is safe - `2b8c303` -> `4cccefe`) replacing the em-dash
  with a plain hyphen. Independently re-verified by the orchestrator (not
  just trusting the maker's own report): `git diff 3e91d08...HEAD -- ...`
  shows zero added lines matching an em/en-dash across all three files, and
  `git diff 3e91d08...HEAD --stat` still shows exactly the same 3 files with
  the same insertion counts as before the fix (64 insertions total across
  `ci.yml`/`CONTRIBUTING.md`/`SECURITY.md`) - the fix changed content, not
  scope.
- Round 2: not needed - the single finding was the only one, already fixed
  and re-verified; no further reviewer pass was dispatched since the fix was
  a mechanical one-line correction with an independent orchestrator re-check
  standing in for a full second review round.

No other findings (spec-compliance, scope-creep check, RULES.md invariant
scan) from either the reviewer or the orchestrator's own independent
spot-checks.

Anneal: one line appended to RULES.md "Proposed by the loop" - generated
prose needs an explicit em/en-dash `grep` before the maker reports a docs
change as done, not left solely to the reviewer to catch (FORGE #73). See
`RULES.md`.

Gate (`.claude/loop.yaml` `gate_cmd`): `ruff check src/axon/router
src/axon/resilience tests/router tests/resilience` -> `[]`, exit 0 (no
findings). `pytest tests/router tests/resilience tests/store tests/scripts
tests/cli tests/doctor -q` -> **341 passed, 0 failed, 1 skipped** (run twice:
once before the review fix round, once after the maker's amend - identical
result both times). No pre-existing shared-Postgres-contention failures were
observed on either run this pass (unlike issues #67/#68/#71, which sometimes
saw 6 such failures from a concurrent benchmark arm) - consistent either way
with non-regression, since this pass's diff (CI-workflow + docs only)
touches no code path the gate's test suite exercises.

Gate-coverage note: `gate_cmd` runs `ruff`/`pytest` only - it does not lint or
otherwise exercise `.github/workflows/ci.yml`, `SECURITY.md`, or
`CONTRIBUTING.md` (no YAML linter or GitHub-Actions-specific check is part of
this repo's gate). This is inherent to any CI-workflow-only issue (same as
#67/#70/#71) and was covered instead by direct command execution
(`yaml.safe_load` structural checks, local server boot + readiness/teardown
re-verification, `gh api` version-pin confirmation) rather than the
pytest-based gate.

Residual gap (same class #71 flagged): this pass does not `git push` or open
a PR, so the shipped `dast` job has not been verified running inside a real
GitHub Actions execution - only its individual mechanisms were validated
locally (server boot/readiness/teardown with the exact commands and env the
job uses; YAML structural correctness; the `zaproxy/action-baseline@v0.15.0`
and `ghcr.io/zaproxy/zaproxy:2.17.0` version pins independently confirmed to
be real, published, current-stable tags via `gh api`). Specifically NOT
verified locally (would require a real Actions runner): the actual `docker
run --network="host" ...` invocation succeeding end-to-end against the
backgrounded server, and the artifact actually appearing in a real PR's
Actions run. Confirm this once the real PR is opened and CI actually runs
(orchestrator note: this benchmark pass does not open that PR itself).

Report: .specs/features/73-dast-zap-baseline-scan/validation.md
