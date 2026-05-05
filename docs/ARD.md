# Prometheus ARDs

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
