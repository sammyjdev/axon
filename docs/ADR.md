# AXON ADRs

Status: active public summary

This document captures the architectural decisions that are still relevant to
external users and contributors. Historical planning material is kept in the
private source repository and is not required to understand or run the public
project.

## ADR-001: Separate data and engine paths

- Decision: keep the knowledge vault outside the engine repository.
- Definition:
  - `AXON_VAULT=~/vault`
  - `AXON_ENGINE=/path/to/axon`
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

- Status: partially revoked by dec-101 (Neo4j dropped; see docs/decisions/).
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

- Decision: use Docker Compose with Qdrant, Redis, Postgres, Langfuse, and
  Ollama, with CPU/GPU profiles. (Neo4j was evaluated and dropped per dec-101.)
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
- Consequence: AXON may keep the original context when compression is not
  trustworthy.
- Rationale: token savings are only useful when retrieval fidelity survives the
  compression pipeline.

## ADR-011: AXON stays agent-agnostic; Odisseu is an optional consumer

- Decision: position AXON as a self-hosted context, memory, and
  governance engine for AI systems rather than as a deep agent runtime.
- Shape:
  - AXON exposes CLI, MCP, and future API/profile surfaces;
  - Odisseu may consume AXON, but AXON must remain useful without
    Odisseu;
  - Odisseu must remain free to support non-AXON backends.
- Rationale: deep-agent-first positioning would narrow adoption, overfit the
  engine to one consumer, and reduce AXON's value as shared
  infrastructure for other developers and teams.

## ADR-012: Distribution-first roadmap for external developer adoption

- Decision: the next product phase prioritizes installation, hardware-fit,
  profiles, and guided customization over new deep-agent-specific features.
- Shape:
  - support modes: `full-local`, `hybrid-local`, `remote-infra`, `minimal`;
  - support targets: macOS, Linux, Windows/WSL2, CPU-first and GPU-capable
    machines;
  - provide `pb init`, `pb doctor`, profiles, and profile-driven defaults
    before broader domain-pack expansion.
- Rationale: AXON only becomes a credible shared tool when another
  developer can install, size, and use it without inheriting the author's
  machine assumptions.
