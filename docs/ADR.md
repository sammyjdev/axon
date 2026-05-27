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

- Status: extended by dec-106 (concrete models now come from the active
  provider profile; tier shape preserved).
- Decision: route cloud models by task type with an explicit fallback.
- Definition (tier shape):
  - trivial/completion -> Haiku-class model
  - code analysis -> Sonnet-class model
  - architecture/deep reasoning -> Opus-class model
  - fallback -> Haiku-class model
- Concrete models per profile:
  - PAID: `openrouter/anthropic/claude-{haiku,sonnet,opus}-4` (D2 verbatim)
  - FREE: `groq/llama-3.1-8b-instant`, `groq/llama-3.3-70b-versatile`,
    `nvidia_nim/meta/llama-3.1-70b-instruct`
- Rationale: keeps cost and quality predictable; profile system lets users
  pick between zero-spend free tiers and paid Claude tiers without changing
  the tier semantics.

## ADR-003: Local Ollama models

- Status: opt-in as of dec-106 (default `AXON_PROVIDER_OLLAMA=0`).
- Decision: standardize on lightweight local models for classification and
  compression, with heavier models reserved for larger hardware.
- Default models when enabled:
  - `phi3:mini`
  - `gemma4:e4b`
  - `gemma4:26b`
- Rationale: reduce cloud cost and preserve low-latency local operation.
  Default off because the recommended onboarding hardware (16 GB laptop)
  does not run these models comfortably; users with capable hosts re-enable
  explicitly.

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

## ADR-013: Tool risk classification, policy gate, and tracing middleware

- Status: accepted via dec-109.
- Decision: every MCP tool carries a risk class (`read` / `write` /
  `destructive`) enforced by a single decorator that also emits standard
  `invoke` / `policy` / `output` / `error` trace stages under a shared
  `trace_id`.
- Mechanism:
  - `@traced_tool(risk=...)` wraps every tool; reads skip the policy gate;
    writes deny on RESTRICTED ctx; destructive additionally require
    `AXON_ALLOW_DESTRUCTIVE` truthy.
  - `PolicyRegistry.decide_tool_action` returns a `PolicyDecision` and
    emits a `ComplianceEvent` through the same audit channel as cloud
    routing.
  - `on_commit` is idempotent by `(repo, git_hash)`; `Decision.judged`
    distinguishes "unscored" from "judged with score 0.0".
- Rationale: makes AXON usable as a harness primitive — uniform
  observability and a single guardrail surface instead of per-tool
  ad-hoc checks. Extends ADR-006 (restricted-context isolation) to the
  tool layer.
- Reference: [`dec-109`](decisions/dec-109-tool-tracing-and-risk-gating.md).
