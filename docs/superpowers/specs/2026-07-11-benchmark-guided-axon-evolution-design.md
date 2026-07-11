# Benchmark-Guided AXON Evolution Design

**Date:** 2026-07-11
**Status:** Approved design

## Goal

Evolve AXON through small, independently verifiable changes. Fix confirmed contract gaps first, reuse the existing benchmark harness, and require hermetic evidence before adding retrieval techniques or external evaluation gates.

This design keeps the work in the repository:

- This spec owns stable scope, ordering, and benchmark policy.
- `docs/agent-backlog.md` owns execution state.
- GitHub Issues are deferred until work needs external coordination, cross-repository dependencies, or public tracking.

## Current System Boundary

AXON remains a context engine and MCP provider for an external agent. It does not become a ReAct runtime, embedded chat application, or evaluation framework.

The relevant flows are:

```text
Indexing
file -> structural chunking -> embedding -> pgvector/HNSW
     -> optional lexical/RRF -> Postgres symbol graph

MCP ask
external agent -> retrieval -> optional rerank -> bounded correction
               -> guarded compression -> prompts -> external agent

HTTP evaluation
external evaluator -> initial retrieval -> completion -> contexts + telemetry
```

The MCP and HTTP paths share initial retrieval but intentionally diverge afterward. Benchmarks must name the exact variant they measure instead of referring to a generic AXON pipeline.

## Design Principles

1. Fix false-positive validation before expanding validation capability.
2. Treat unavailable or stale evidence as indeterminate, never successful.
3. Reuse existing trace, benchmark, fixture, and gate infrastructure.
4. Keep deterministic checks in CI and provider-dependent evaluation outside CI.
5. Add retrieval techniques only after a benchmark isolates a measured loss.
6. Keep each backlog item independently verifiable.

## Work Graph

```text
L1-0 (P0, ready) -> L1-1 (P1, blocked by owner decision)

OPS-1 (P1, ready)

HTTP-1 (P1, ready) -> GNOMON external validation

EMB-1 (P2, reactivate existing item) -> future retrieval changes
```

`L1-0`, `OPS-1`, `HTTP-1`, and `EMB-1` are independent. They must not be given artificial dependencies.

## Task L1-0: Make L1-full Revalidation Honest

**Priority:** P0
**Size:** Small
**State:** Ready
**Blocked by:** Nothing

### Problem

`l1_full()` currently returns unconditional success with stub metadata. `axon adr validate-drafts` then updates `last_l1_full_at`, clearing `stale-pending` without structural validation. This contradicts dec-111 and creates a false safety signal.

### Behavior

Until a trustworthy structural lookup is available, L1-full returns an indeterminate result. An indeterminate result preserves the draft and its stale state. It does not validate, promote, demote, or renew the L1-full timestamp.

### Subtasks

1. Add a regression test proving the current stub clears `stale-pending` incorrectly.
2. Represent `validated`, `demoted`, and `indeterminate` as explicit outcomes. Use the smallest type consistent with existing ADR gate models.
3. Make the unavailable stub return `indeterminate`.
4. Update the CLI and git-event callers to share the same transition semantics.
5. Report separate counts for all three outcomes.
6. Update existing tests that currently assert stub success.

### Acceptance Criteria

- An unavailable L1-full backend leaves `last_l1_full_at` unchanged.
- The draft remains active and `stale-pending`.
- CLI and hooks report `indeterminate` without reporting validation or promotion.
- Internal lookup failures fail safe as indeterminate and do not demote drafts.
- L1-light behavior remains unchanged.
- ADR gate, CLI, hook, and doctor tests pass.

### Verification

```bash
rtk pytest tests/adr/gates/test_l1.py tests/adr/test_clis.py tests/doctor/test_checks_adr.py -q
```

This is a correctness gate, not a retrieval benchmark.

## Task L1-1: Connect L1-full to the Structural Index

**Priority:** P1
**Size:** Medium
**State:** Blocked
**Blocked by:** L1-0 and an owner decision about index authority and freshness

### Decision Gate

Before implementation, decide:

- Which existing Postgres-backed structure is authoritative for symbol existence.
- How repository identity participates in the lookup key.
- How index freshness is established for the evaluated repository revision.
- How path candidates and symbol candidates are interpreted.

An absent, ambiguous, or stale index returns `indeterminate`. Only an authoritative and current index may prove a candidate absent.

### Subtasks

1. Record the owner decision about authority, repository scope, and freshness.
2. Add contract tests with a fake lookup for present, absent, unavailable, stale, and cross-repository collision cases.
3. Add a thin adapter to the chosen existing index.
4. Validate extracted path and symbol candidates through the adapter.
5. Feed the result into the shared transition from L1-0.
6. Return checked and missing candidates as diagnostic details.

### Acceptance Criteria

- Present candidates in the correct repository return `validated`.
- Absent candidates in a proven-current index return `demoted` with details.
- Missing, stale, ambiguous, or unavailable index state returns `indeterminate`.
- Only `validated` updates `last_l1_full_at`.
- CLI and hooks use one transition implementation.
- Tests cover cross-repository symbol collisions.

### Verification

Run the L1-0 checks plus focused tests for the selected repository adapter. Retrieval recall is not a valid proxy for structural ADR validation.

## Task OPS-1: Align the Operational Contract with Postgres

**Priority:** P1
**Size:** Small
**State:** Ready
**Blocked by:** Nothing

### Problem

dec-121 and `AGENTS.md` define Postgres as the unified backend, while active rules, health output, help text, and operational documentation still present SQLite, Redis, or Qdrant as current runtime components.

### Subtasks

1. Update `RULES.md` so it no longer promises SQLite rollback.
2. Remove retired backend choices from active runtime configuration surfaces.
3. Rename health output and help text to match the probes actually executed.
4. Update active operational documentation and runtime docstrings.
5. Preserve historical decisions and plans as historical records.
6. Add or update tests for health labels and CLI help.
7. Run a scoped scan for active references to retired runtime components.

### Acceptance Criteria

- Active rules and configuration describe Postgres and pgvector as the runtime backends.
- `axon health` and its help name Postgres, pgvector, vault, and git correctly.
- Active operational documentation does not instruct users to configure SQLite, Redis, or Qdrant.
- Historical ADRs and migration plans remain unchanged.
- Existing health probes are retained. Only their false labels and contracts change.

### Verification

```bash
rtk pytest tests/mcp/test_axon_tools.py tests/cli/test_axon_cli.py -q
rg -n 'QDRANT_URL|REDIS_URL|sqlite: ok|SQLite graph|rollback SQLite' \
  RULES.md AGENTS.md docs/SECOND_BRAIN.md src/axon
```

Review scan matches manually because historical comments or compatibility guards may be legitimate.

## Task HTTP-1: Correlate HTTP Evaluation with Retrieval

**Priority:** P1
**Size:** Medium
**State:** Ready
**Blocked by:** Nothing for implementation; external GNOMON validation is blocked by this task

### Problem

`POST /v1/chat/completions` performs retrieval and records token usage, but it does not provide a correlation identifier connecting the response, retrieval decision, and `RecallRecord`. An evaluator cannot diagnose a result per request.

### Behavior

For requests with `include_context=true`, create one HTTP trace, record one retrieval stage using the existing trace format, return its identifier, and persist the same identifier in `RecallRecord`.

Telemetry contains only non-sensitive aggregate metadata. It must not contain the raw query or retrieved segment text.

### Subtasks

1. Add failing contract tests for `include_context=true` and `false`.
2. Generate or propagate a request-scoped `trace_id`.
3. Measure retrieval duration and aggregate hit count.
4. Record the available retrieval strategy metadata in the existing trace store.
5. Add the same `trace_id` to `RecallRecord`.
6. Return additive correlation metadata in the HTTP response.
7. Preserve non-fatal behavior when telemetry persistence fails.

### Acceptance Criteria

- Every context-enabled response has a non-empty `trace_id`.
- Exactly one corresponding `RecallRecord` contains the same identifier.
- The trace has one retrieval stage with non-negative duration and hit count.
- Strategy metadata is included when available.
- Context-disabled requests preserve `contexts=[]` and do not fabricate retrieval hits or strategy.
- Telemetry contains neither the query nor retrieved text.
- The HTTP contract remains compatible with GNOMON.

### Verification

```bash
rtk pytest tests/http/test_chat_completions.py tests/observability/test_recall_telemetry.py -q
```

## Task EMB-1: Reactivate the Existing Hermetic Retrieval Gate

**Priority:** P2
**Size:** Small
**State:** Ready after baseline confirmation
**Blocked by:** A green baseline for the added test directories

### Scope

Reuse the existing `EMB-1` backlog item. Do not create a second harness, golden set, or retrieval-eval implementation.

The gate covers deterministic embedder tests and the existing retrieval-eval smoke. PostgreSQL sweeps, GPU probes, provider calls, and GNOMON remain outside the standard CI gate.

### Subtasks

1. Run the candidate test sets on the current main branch.
2. Classify any failure as product regression, environment dependency, or unrelated pre-existing debt.
3. Make only the minimum changes needed for hermetic execution.
4. Add the deterministic embedder and retrieval-eval commands to the gate.
5. Assert non-zero test collection for each added test group.
6. Document the boundary between the hermetic gate and live recall guard.

### Acceptance Criteria

- The gate runs without `.env`, credentials, network, Postgres, or GPU.
- Embedder and retrieval-eval test collection is non-zero.
- The existing golden fixture and evaluator remain the source of truth.
- Metrics remain deterministic across repeated local runs.
- The live recall guard remains opt-in and outside the merge gate.

### Verification

Use the existing EMB-1 commands after confirming their current definitions in `docs/agent-backlog.md`. Do not copy command lists into a second source of truth.

## Benchmark Ladder

### Level 0: Focused Correctness Checks

Each task leaves the smallest deterministic check that proves its own behavior. L1 and operational contract changes do not use retrieval metrics.

### Level 1: Hermetic Retrieval Smoke

The reactivated EMB-1 gate uses frozen fixtures and injected fakes. It detects contract regressions in retrieval and bounded correction without external services.

Expected metrics are the ones already produced by the evaluator, including recall before and after correction, delta, retry rate, and give-up rate. These numbers describe the fixture, not production quality.

### Level 2: Hermetic Reporting by Query Class

Add query-class reporting only when a concrete retrieval change needs isolation. Prefer extending existing fixture metadata and reporting over creating another suite.

Possible classes include exact symbol, path, structural relation, and natural-language code query. Class thresholds are established from a measured baseline, not invented in this design.

### Level 3: Live Recall Guard

Use the existing opt-in guard against a real indexed corpus. Record commit, corpus identity, configuration, model/provider identity, and result. This level is diagnostic and must not become a default CI dependency.

### Level 4: GNOMON External Evaluation

Run only after HTTP-1 provides request correlation. Record:

- Commit and evaluation variant.
- Golden-set version and corpus identity.
- Provider and usage source.
- Sample trace identifiers.
- Retrieval and downstream evaluation results.

The run is invalid for provider-cost comparison when usage is estimated. External credentials, private corpus content, and raw evaluation output remain outside the repository.

## Promotion Rules

A retrieval technique may enter the backlog only when:

1. A named benchmark level reproduces a loss.
2. The loss is isolated to a query class or pipeline stage.
3. A proposed change has a measurable success criterion.
4. The change preserves or improves unaffected classes.

BM25 changes, HyDE, multi-query, contextual embeddings, parent expansion, embedding fingerprints, and dynamic task classification remain out of scope until these conditions are met.

## Failure and Rollback Policy

- Correctness and hermetic checks block the related change.
- Live and GNOMON failures produce diagnostic evidence but do not modify CI state automatically.
- A benchmark result never rewrites its own baseline. Baseline changes require an explicit reviewed change with before-and-after evidence.
- If new instrumentation fails to persist, the user request still completes and the failure is logged without leaking sensitive content.

## Backlog Integration

After this spec is accepted:

1. Add `L1-0`, `L1-1`, `OPS-1`, and `HTTP-1` to `docs/agent-backlog.md`.
2. Keep `EMB-1` under its existing identifier and reactivate it after confirming the baseline.
3. Link each item back to this spec instead of copying benchmark policy.
4. Keep execution state only in the backlog.

GitHub Issues remain deferred. Reconsider them when a task requires an external owner, cross-repository scheduling, or public coordination.

## Explicit Non-Goals

- Agent runner, embedded chat, LangGraph, or transcript ownership.
- RAGAS or GNOMON inside AXON core.
- New vector, graph, telemetry, or benchmark storage.
- A second golden set or benchmark harness.
- Reintroduction of SQLite, Redis, or Qdrant compatibility.
- Retrieval techniques without a reproduced benchmark loss.

## Completion Definition

This evolution cycle is complete when:

- L1-full can no longer report false validation.
- The active operational contract matches the Postgres runtime.
- HTTP evaluation results can be correlated to a concrete retrieval trace.
- The existing hermetic retrieval smoke runs in the gate.
- External GNOMON runs can be diagnosed per request without moving evaluation into AXON core.
