# dec-102 — Cut scope: freeze router cloud profiles

- Status: accepted
- Date: 2026-05-21

## Context

The runtime carries a configurable cloud-router profile surface (installation
profiles, modes, multi-provider routing knobs in `config/runtime.py` and
`router/`). The AXON repositioning focuses effort on cross-agent context
continuity. That configurability is breadth which does not serve the launch
focus.

## Decision

Freeze the router cloud-profile feature surface at its current capability:

- Keep D2 task-based routing (Haiku / Sonnet / Opus by task type) — it is used
  by the LLM-judge in Phase 5.
- Do not extend profiles, modes, or provider configuration during the AXON
  refactor.
- New work routes through the existing `router/engine.py` rather than adding
  configuration surface.

## Rationale

- Scope discipline: the launch thesis is context continuity, not router
  flexibility.
- Existing routing is sufficient for the LLM-judge and internal LLM calls.

## Consequences

- The `pb profile` (now `axon profile`) CLI group is kept but not expanded.
- Profile/mode feature requests are deferred post-launch.
