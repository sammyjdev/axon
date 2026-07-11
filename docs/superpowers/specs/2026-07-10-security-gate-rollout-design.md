# Security Gate Rollout (axon, glyph-kg, gnomon-eval)

## Context

None of the three Python repos onboarded to the agentic loop that share a
security posture (`axon`, `glyph-kg`, `gnomon-eval`) currently run any
security-focused static or dynamic analysis. All three gate on `ruff` (lint)
plus `pytest`; `glyph-kg` additionally runs `mypy`. None use the `pre-commit`
framework — `axon` has a bespoke hook installer (`axon hooks install`,
referenced in `SECURITY.md`) that this rollout supersedes with the standard
`pre-commit` framework.

Only `axon` exposes an HTTP surface (FastAPI + uvicorn, serving its MCP
server). `glyph-kg` and `gnomon-eval` are CLI/library tools with no network
listener.

`axon` and `gnomon-eval` are already onboarded to the FORGE agentic loop
(`.claude/loop.yaml` present). `glyph-kg` has the Pocock-style
`docs/agents/{domain,issue-tracker,triage-labels}.md` setup but is missing
the FORGE half (`.claude/loop.yaml`).

## Goal

Get all three repos to a security gate (pre-commit + CI) covering the same
pattern, and use the rollout in `axon` as a benchmark comparing FORGE's two
native harnesses — Claude Code and Codex (`FORGE_HARNESS=codex`) — on
identical work.

## Non-goals

- Building new benchmark tooling. FORGE already dispatches through either
  harness (see `~/.claude/agents/forge/references/harness-codex.md`); this
  rollout only uses that existing capability.
- Full DAST coverage. `glyph-kg` and `gnomon-eval` have no HTTP surface, so
  dynamic analysis there is limited to property-based testing.
- Rolling out `glyph-kg` and `gnomon-eval` issues in this pass (see Phasing).

## Security pattern (applies per repo, DAST only where noted)

### Static

| Tool | Runs | Covers | Notes |
|---|---|---|---|
| `ruff --select S` | pre-commit + CI | SAST (flake8-bandit ruleset — injection, unsafe `eval`/`pickle`/`subprocess`, etc.) | Already installed everywhere; just extends `[tool.ruff.lint]`. Ships in report-only mode first, findings triaged, then flipped to blocking — same incremental pattern `axon`'s `ci.yml` already uses for its router/resilience-scoped `ruff check`. |
| `gitleaks` | pre-commit + CI (full-history) | Secret scanning | Pre-commit hook via the official `gitleaks/gitleaks` mirror (no baseline file to maintain). CI job is a safety net for commits that bypass the local hook. |
| `pip-audit` | CI only | Known CVEs in installed dependencies | Deliberately excluded from pre-commit — it needs network access, and a commit-time hook that can fail on network flakiness is the fastest way to get pre-commit disabled. |

### Dynamic

| Repo | What | Where | Blocking? |
|---|---|---|---|
| `axon` | ZAP baseline scan against the FastAPI/MCP server (started via `docker-compose` or `uvicorn` in-job) | CI, new job | No initially — baseline scans have a high false-positive rate; report as artifact, promote to blocking after a reviewed baseline. |
| all three | `hypothesis` property-based tests on the single highest-risk parsing/validation entry point per repo (config parser, external input, etc.) | CI (regular pytest) | Yes — it's new test code, not a scan. |

Hypothesis scope is deliberately one or two named functions per repo, decided
during issue triage — not "add hypothesis everywhere."

## Pre-commit adoption

Each repo gets a `.pre-commit-config.yaml` running `ruff` (existing lint +
`S` ruleset) and `gitleaks`. This supersedes `axon`'s bespoke
`axon hooks install` mechanism with the standard framework — declarative,
portable across contributors, no custom script to maintain.

## Phasing

**Phase 1 (this rollout): `axon`.** Full backlog below, published as issues
now. Chosen first because it's the richest surface to validate the pattern
on (HTTP server for DAST, existing bespoke hook to retire, documented
secrets posture) and because it's the benchmark target.

**Phase 2 (documented, not yet ticketed): `glyph-kg` and `gnomon-eval`.**
Same pattern, minus DAST. `glyph-kg` needs FORGE onboarding
(`.claude/loop.yaml`) first — its `docs/agents/*` Pocock setup already
exists. Issues for phase 2 are cut after the `axon` pattern is validated,
to avoid rework if triage in phase 1 changes the approach (e.g. `ruff S`
finding volume, DAST approach).

## Axon backlog (phase 1)

| # | Issue | Depends on |
|---|---|---|
| 1 | `.pre-commit-config.yaml` (ruff + gitleaks) | — |
| 2 | `ruff --select S` in report mode, triage findings | — |
| 3 | `ruff S` blocking in CI | 2 |
| 4 | `pip-audit` CI job | — |
| 5 | `gitleaks` full-history CI job | — |
| 6 | `hypothesis` tests on the highest-risk parsing entry point | — |
| 7 | DAST job (ZAP baseline vs FastAPI/MCP server), non-blocking | — |
| 8 | DAST promoted to blocking, after baseline review | 7 |

(A 9th slot is reserved during `to-issues` triage if the vertical-slice pass
splits any of the above — the count is a planning estimate, not a hard cap.)

## Benchmark: Codex vs Claude Code

All `axon` phase-1 issues run through **both** FORGE harnesses — Claude Code
(default) and Codex (`FORGE_HARNESS=codex`) — in separate worktrees,
producing a separate PR per harness per issue.

Where an issue depends on another (3→2, 8→7), the dependency is resolved
**within the same harness** — Codex's issue 3 builds on Codex's issue 2, not
Claude's, to keep the comparison uncontaminated.

Execution happens in the user's existing FORGE-driving terminal/session, not
this one. This session's deliverable is the published PRD and issues; `forge
task N` (run per-harness) is invoked separately.

After all pairs complete, a single ADR is captured in `axon` via `save_adr`
consolidating the comparison across all issue pairs: time, estimated cost,
diff size, reviewer findings, rework rate.

## Testing

Each issue's own gate (`ruff`, `pytest`, the new CI jobs) is the acceptance
check — no separate test suite for the rollout itself. The DAST and
`pip-audit` jobs are validated by intentionally triggering a known-bad case
once (e.g. a fixture with a fake leaked-looking secret for gitleaks) during
issue implementation, then removing the fixture.
