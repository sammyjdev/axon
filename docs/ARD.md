# AXON ARDs

Status: active public summary

This document captures the architectural requirements that matter to users and
contributors of the public project. Internal planning history is intentionally
excluded from the public repository.

## ARD-001: Context isolation

- Default searches must exclude restricted contexts.
- Restricted data must require explicit context selection.

## ARD-002: Traceable local memory

- Architectural decisions must be persisted in a local store.
- Session memory must support continuity across work sessions.

## ARD-003: Structure-aware chunking

- Chunking must follow language structure, not raw file slicing.
- Chunker changes must keep the fixture suite green.

## ARD-004: Budget-aware routing

- The router must enforce budget limits before provider calls.
- Model fallback must remain deterministic under budget pressure.
- The router must enforce per-provider rate limits before provider calls
  (dec-106). Rate-limited calls must fail with `DENY_RATE_LIMIT` and must
  not be recorded as model failures (the circuit breaker stays closed).
- Rate limits are configurable per provider via
  `AXON_<PROVIDER>_MAX_RPM` and `AXON_<PROVIDER>_MAX_RPD`.

## ARD-005: Cross-platform operation

- Setup must work on both CPU-first and GPU-capable local environments.
- Runtime configuration must avoid machine-specific assumptions.

## ARD-006: Minimum observability

- The local stack must expose enough telemetry to inspect cost, latency, and
  service health.

## ARD-007: Validated compression output

- CLI and MCP must send complete retrieval chunks into the compression
  pipeline.
- Compression output must be rejected if it drops required anchors or contains
  meta-instruction leakage.
- Safe fallback must preserve the last trusted context.

## ARD-008: Bounded retrieval

- Retrieval must support bounded output through token and scope constraints.
- Dependency traversal must stay budget-aware to avoid context bloat.

## ARD-009: Mode-aware distribution

- AXON must support at least four documented operating modes:
  `full-local`, `hybrid-local`, `remote-infra`, and `minimal`.
- Setup and runtime configuration must not assume one machine shape or one OS.
- The supported path for Windows must be documented explicitly, including WSL2
  when native support is incomplete.

## ARD-010: Hardware-fit diagnosis

- AXON must detect when the current machine is undersized for the chosen
  operating mode.
- The product must recommend a safer mode when local infra or local model cost
  is too high.
- Diagnostics must cover Docker, local model reachability, and approximate
  machine capability.

## ARD-011: Guided customization

- AXON must be configurable from user problems and constraints, not only
  from raw infrastructure toggles.
- The product must be able to recommend which subsystems are necessary and
  which are overkill for a given user profile.
- Profile selection must be reproducible rather than ad hoc.
