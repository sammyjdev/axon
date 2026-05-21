# dec-100 — Rename Prometheus to AXON

- Status: accepted
- Date: 2026-05-21

## Context

The engine matured under the name "Prometheus" as a self-hosted context engine.
The project is being repositioned as an agent-agnostic cross-agent context
network — continuity of context across Claude Code, Codex, and Cursor. The name
"Prometheus" collides with the well-known monitoring system and does not
communicate the new positioning.

## Decision

Rename the project, Python package, and CLI:

- Python package: `prometheus` → `axon` (`src/prometheus/` → `src/axon/`)
- Distribution name: `prometheus-engine` → `axon-mcp`
- CLI entry point: `pb` → `axon`
- Environment variables: `PROMETHEUS_*` → `AXON_*`
- Default runtime DB: `prometheus.db` → `axon.db`
- GitHub repository: `Prometheus` → `axon`

The rename is executed first, before any behavioral change, so the regression
suite isolates naming churn from logic churn.

## Rationale

- "AXON" (Agent-agnostic eXecution & cOntext Network) states the positioning.
- A single mechanical rename early avoids naming churn across later phases.

## Consequences

- The config filename `prometheus.toml` is intentionally **not** renamed here;
  it is tracked as a separate follow-up.
- `PROMETHEUS_*` env vars are renamed **without** a compatibility fallback;
  existing `.env.local` files must be updated.
- The local working directory path is not renamed (left to the operator).
- `data/prometheus.db` is moved to `data/axon.db` to preserve existing data.
- Verified: full test suite (383 tests) passes after the rename.
