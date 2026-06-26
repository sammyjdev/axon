# dec-107 - Validation strategy via GitHub Actions

- Status: accepted
- Date: 2026-05-25

## Context

Until this branch the repository **had no CI configured**: no
`.github/workflows/*.yml`, no pre-commit hooks. Validation depended on whoever
ran `pytest` locally before pushing. With the introduction of the profile system
(dec-106), there are now **two execution paths** (`AXON_PROVIDER_PROFILE=free|paid`)
that need to be validated on every change - risk increases without CI.

## Decision

Create `.github/workflows/ci.yml` with 4 parallel jobs, running on PRs and on
pushes to `master`/`main`:

| Job | What it does | Why |
| --- | --- | --- |
| `lint` | `ruff check src/ tests/` | Catches style issues and common bugs before the test runner does |
| `compile` | `python3 -m compileall src` | Catches syntax errors in <2s, without installing deps |
| `test` | `pytest tests/router tests/resilience` in matrix `[py3.11, py3.12] × [free, paid]` | Ensures both profiles work and that tier downgrade is profile-agnostic |
| `profile-smoke` | Imports `axon.router.engine`, verifies `_MODEL_MAP` resolved by the profile | Detects profile registration breakage without running the full pytest suite |

### Test scope in CI

CI runs **only** `tests/router` and `tests/resilience` for now. Rationale:

- These are the modules directly affected by dec-106
- Clear guarantee that both profiles pass
- The rest of the repo has infrastructure dependencies (Qdrant, Redis, mem0,
  tree-sitter-java) that require more setup

A broader suite will be added to CI as each area is verified stable on an Ubuntu
runner (separate issue per area).

### Concurrency

`concurrency.cancel-in-progress: true` cancels old runs when a new commit
arrives at the same branch. Saves runner minutes during fast PR iteration.

### Cache

`actions/setup-python@v5` with `cache: pip` and
`cache-dependency-path: pyproject.toml` cuts ~80% of install time on subsequent
runs.

## Rationale

- **Profile matrix is non-negotiable.** The most likely regression after
  dec-106 is "someone changes a model string and breaks a profile without
  noticing". The matrix catches this on every PR.
- **Python 3.11 + 3.12 matrix.** `pyproject.toml` declares `>=3.11`; CI
  must prove this, not assume it.
- **Workflow starts lean.** 4 simple jobs are preferable to a complex pipeline
  that nobody understands. Grows as needed.
- **dec-102 preserved.** Workflow does not add configuration surface to the
  router; it only validates what exists.

## Consequences

- **Zero lint debt as a prerequisite.** Pre-existing UP042 findings in
  `policy/core.py`, `circuit_breaker.py`, `expansion/budget.py`,
  `expansion/scoring.py` migrated to `StrEnum`. Chunker fixtures
  (ADR-005/D5) excluded via `per-file-ignores`.
- **Workflow promoted to `.github/workflows/ci.yml` on 2026-05-27.** The
  intermediate file `docs/ci-workflow-proposed.yml` was removed.
  Lint runs only on `src/axon/router src/axon/resilience tests/router
  tests/resilience` for now - the broad scope has ~22 pre-existing UP/I001
  findings; expansion tracked as TODO in the workflow.
- **No secrets in CI for now.** The FREE profile needs `GROQ_API_KEY` and
  `NVIDIA_NIM_API_KEY` for real calls, but the `test_router` and
  `test_resilience` tests mock those calls - no keys needed. If we ever
  want a real smoke job against Groq, then add the secret.
- **Rate limit gate is testable without network.** `test_classifier_raises_when_rate_limited`
  mocks `_RATE_LIMITER` and verifies `DENY_RATE_LIMIT` before any LiteLLM
  call. Covered.

## Out-of-scope

- **Coverage.** `pytest-cov` is in `[dev]` but we are not running coverage in
  CI for now. Can be added when there is a clear target (e.g. 80% in
  `src/axon/router/`).
- **Integration tests against real providers.** Hitting Groq from CI requires
  a secret + budget. Worth it when the product stabilizes.
- **Pre-commit hooks.** CI covers the case. Hooks are optional and the
  responsibility of the local developer.
- **Docs validation (link check, etc.).** Not worth the maintenance cost now.

## Migration

Completed on 2026-05-27. Workflow lives in `.github/workflows/ci.yml`.
Subsequent edits via normal push.
