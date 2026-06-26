# dec-110 - Declarative memory requires a lightweight signal in the commit

- Status: accepted
- Date: 2026-05-27

## Context

Automatic ADR inference from each commit (`pb adr
infer-commit`, implemented in `src/axon/cli/pb.py:1439`) currently fires on
every post-commit hook. ~95% of commits are not architectural
(bugfixes, deps, formatting, minor refactor) but the LLM is still called
and produces JSON. Result: vault noise, token cost, and the primary
"hallucinated ADR" vector identified by red-team R1.

Five red-team rounds considered: confidence-score (rejected,
not calibrated), always-on inference with a gate (partial, but LLM
call cost persists), explicit handshake like `axon snapshot
--adr` (rejected, destroys event-driven model). Converged on a
lightweight commit signal as the discriminator.

## Decision

ADR inference fires only when the commit contains one of the explicit
signals below. Without a signal, the derived capture (`CodeChange`) persists
normally; ADR inference is not executed.

| Signal | Status | Compatibility |
|---|---|---|
| `arch:` subject prefix | **primary** | Conventional Commits via `type-enum` |
| `decision:` subject prefix | accepted synonym | same |
| `ADR-Decision: <title>` trailer in body | **optional metadata** | always compatible |
| `pb adr infer-commit --force` | manual escape hatch | n/a |

The trailer is **not** the canonical path - it exists for supplemental
annotation that AXON consumes if present, but no other tooling needs to
understand it. This avoids conflict with strict `commitlint` `type-enum` and
`semantic-release` parsers.

## Rationale

- **Subject prefix `arch:` is Conventional-Commits-friendly**: integrates
  with the ecosystem via trivial configuration (`'type-enum': [2, 'always',
  [..., 'arch', 'decision']]`).
- **Trailer outside the canonical path**: users with strict `commitlint`/
  `semantic-release` can use the trailer without breaking their pipeline;
  AXON parses it silently.
- **Signal replaces probabilistic inference**: the developer signals
  explicitly when there is an architectural decision. Reduces both LLM
  call cost and hallucination surface.
- **Cost of 5-10 characters per architectural commit** is trivial
  compared to the cost of reviewing noisy drafts.

## Consequences

- `pb adr infer-commit` receives `axon.adr.signal.detect()` at the start and
  returns early if absent.
- `pb commit` optional helper can be added later to suggest the prefix based
  on diff stats.
- Document in `docs/USAGE_GUIDE.md` that declarative capture requires a
  signal.
- Document in [dec-114](dec-114-doctor-diagnostic-first.md) that
  `pb doctor` validates compatibility with the commit toolchain.
- Developer may forget the prefix - workaround: `pb adr add` directly is
  always available. Accepted as residual risk.
