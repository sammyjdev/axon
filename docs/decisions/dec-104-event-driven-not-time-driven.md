# dec-104 — Event-driven capture, not time-driven

- Status: accepted
- Date: 2026-05-21

## Context

Context capture can be triggered on a timer (poll/snapshot) or on events (git
commit/push/init, agent session start/end). Time-driven capture produces noise
and misses the moments that carry decisions.

## Decision

Context capture is driven exclusively by events:

- git events: post-commit, post-push, pre-push, on-init
- agent session lifecycle: session start / end / explicit capture

No background timer or polling loop captures context.

## Rationale

- Commits and session boundaries are where decisions actually crystallize.
- Event triggers keep capture cheap and meaningful; no idle cost.
- Aligns with the existing `pb adr hook` / `infer-commit` mechanism.

## Consequences

- Git hooks must fail silently and never block the git workflow (Phase 3).
- Capture completeness depends on hooks being installed (`axon install-hooks`).
