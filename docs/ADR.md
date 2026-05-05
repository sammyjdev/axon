# Prometheus ADRs

Status: active public summary

This document captures the architectural decisions that are still relevant to
external users and contributors. Historical planning material is kept in the
private source repository and is not required to understand or run the public
project.

## ADR-001: Separate data and engine paths

- Decision: keep the knowledge vault outside the engine repository.
- Definition:
  - `PROMETHEUS_VAULT=~/vault`
  - `PROMETHEUS_ENGINE=/path/to/prometheus`
- Rationale: prevents mixing user data with runtime code and reduces the risk
  of accidental disclosure.

## ADR-002: Task-based model routing

- Decision: route cloud models by task type with an explicit fallback.
- Definition:
  - trivial/completion -> `claude-haiku-4-5-20251001`
  - code analysis -> `claude-sonnet-4-6`
  - architecture/deep reasoning -> `claude-opus-4-7`
  - fallback -> `claude-haiku-4-5-20251001`
- Rationale: keeps cost and quality predictable.

## ADR-003: Local Ollama models

- Decision: standardize on lightweight local models for classification and
  compression, with heavier models reserved for larger hardware.
- Default models:
  - `phi3:mini`
  - `gemma4:e4b`
  - `gemma4:26b`
- Rationale: reduce cloud cost and preserve low-latency local operation.

## ADR-004: Split graph backends by responsibility

- Decision: use Redis for code dependency relationships and Neo4j only for
  Mem0-style memory relationships.
- Rationale: the two graph use cases have different query patterns and
  operational concerns.

## ADR-005: Java chunker quality gate

- Decision: treat the Java chunker as a TDD-first, high-risk subsystem.
- Quality bar:
  - real-world Spring fixtures;
  - explicit chunk boundary assertions;
  - no promotion of chunker changes without a passing suite.
- Rationale: retrieval quality depends directly on chunk fidelity.

## ADR-006: Explicit restricted-context access

- Decision: restricted contexts must never be searched implicitly.
- Mechanism:
  - separate collections by context;
  - explicit context selection for restricted data;
  - CLI and MCP guardrails.
- Rationale: preserve isolation between general knowledge and sensitive context.

## ADR-007: Layered architecture

- Decision: organize the engine into watcher/embedder/store/router/MCP layers.
- Rationale: isolates responsibilities and makes testing and evolution easier.

## ADR-008: Local-first runtime stack

- Decision: use Docker Compose with Qdrant, Redis, Neo4j, Postgres, Langfuse,
  and Ollama, with CPU/GPU profiles.
- Rationale: provide a reproducible local environment across laptops and
  desktops.

## ADR-009: Knowledge split

- Decision: separate rapid capture from deeper reference material.
- Shape:
  - `knowledge/daily` for TILs and quick notes;
  - `knowledge/deep` for durable reference material.
- Rationale: maintain capture speed without losing long-term organization.

## ADR-010: Validated compression

- Decision: context compression is accepted only when it preserves required
  anchors and avoids prompt contamination.
- Consequence: Prometheus may keep the original context when compression is not
  trustworthy.
- Rationale: token savings are only useful when retrieval fidelity survives the
  compression pipeline.
