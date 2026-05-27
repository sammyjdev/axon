# dec-109 — Tool risk classification, policy gate, and tracing middleware

- Status: accepted
- Date: 2026-05-27
- Supersedes: none. Extends ADR-006 (restricted-context isolation) and ARD-006
  (minimum observability).

## Context

Before this work, the 18 MCP tools exposed by `axon serve` had three gaps
that made the engine an uneven harness primitive for external agents:

1. **Uneven observability.** Only `search_code` and `ask` emitted
   `TraceRecord`s, and each created its own `TraceStore` per call. The
   other 16 tools were invisible — no `tool_success_rate`, no per-tool
   latency, no cross-stage correlation by `trace_id`. The runbook for
   agent-harness validation explicitly needs these metrics.
2. **No authorization gate.** `axon_export_now` and `axon_mark_done`
   write to an external Obsidian vault; `axon_capture` writes the engine's
   own decision store. All ran without any per-tool guardrail and ignored
   `ctx` sensitivity — a `ctx=work` write succeeded just as easily as a
   `ctx=knowledge` write.
3. **Unfinished idempotency on capture.** `on_commit` rebuilt a new
   draft `Decision` on every invocation, including retries after a
   transient Qdrant/Redis failure. Replays produced duplicate decisions
   and (because `edges` had no uniqueness constraint) duplicate `touches`
   edges in the graph.

## Decision

Introduce three coordinated primitives:

### 1. `@traced_tool(risk=...)` decorator

A single decorator wraps every MCP tool. It records three trace stages
per call (`invoke`, optional `policy`, then `output` or `error`) under a
shared `trace_id`, sanitizes string args outside an allowlist
(`name_len` + `name_sha8`), and exposes the active recorder via a
`contextvars.ContextVar` so the tool body can append intermediate stages
(`retrieval`, `compression`, `validation_result`) without re-creating the
store. The decorator is applied beneath `@mcp.tool()` so FastMCP's
signature introspection (which follows `__wrapped__`) still sees the
original tool signature.

Three risk classes:

- `read` (11 tools) — never invokes the policy gate.
- `write` (5 tools) — emits a `policy` stage; denied if `ctx` sensitivity
  is RESTRICTED.
- `destructive` (2 tools) — write rules **plus** requires
  `AXON_ALLOW_DESTRUCTIVE` to be set to a truthy value (`1`, `true`,
  `yes`, `on`, case-insensitive). Default is deny.

### 2. `PolicyRegistry.decide_tool_action`

A new method on the existing registry, separate from `decide()` (which
handles cloud routing). It emits a `ComplianceEvent` via the same
`_emit` channel as cloud routing, so a denied destructive call is
auditable through the canonical compliance log — not just the trace
store. New reason codes:

- `DENY_DESTRUCTIVE_NO_CONSENT` — destructive tool called without
  consent env.
- `DENY_RESTRICTED_TOOL_WRITE` — write or destructive call against a
  RESTRICTED ctx.

### 3. Idempotent capture by (`repo`, `git_hash`)

`SessionStore.find_decision_by_git_hash(git_hash, repo=...)` looks up an
existing draft, scoped to the repo to avoid cross-repo SHA collisions
(empty-tree initial commits, cherry-picks). `on_commit` skips creating a
new `Decision` if one exists, but still:

- refreshes `Decision.agent` when `AXON_AGENT` changed between runs;
- re-runs `_link_touched_symbols` (now using a new
  `UNIQUE(source_id, target_id, type)` constraint on `edges` via
  migration `002_unique_edges.sql`, so retries no longer duplicate
  edges);
- re-runs `update_context_file` so the `.axon/context.md` mirror is
  always regenerated.

## Validation aggregate

A new field `Decision.judged: bool` (default `False`) distinguishes
"never judged" from "judged with score 0.0" — `_judge_and_export`
previously used `validation_score == 0.0` as the sentinel and re-judged
forever any decision the LLM rated zero. `validation.aggregate.pass_rate`
counts only judged decisions, raises `ValueError` for `threshold <= 0`,
and now backs the read-only MCP tool `axon_validation_stats` (scope
filterable by repo; `repo=None` aggregates across the workspace).

## Consequences

- Every MCP tool call writes at least two trace records (three for
  write, four for ask). Trace writes are synchronous file appends; the
  hot-path cost is one additional `open(..., 'a')` per call. Acceptable
  at current scale; revisit if it shows up in profiles.
- The two singletons `axon.mcp.server._TRACE_STORE` and
  `axon.hooks.git_event._TRACE_STORE` are bound at import to
  `RuntimeConfig.data_root`. The autouse fixture in `tests/conftest.py`
  redirects `AXON_ENGINE` per test so suite runs don't pollute the
  developer's real data root (rule D1).
- The MCP error model now includes a typed `PolicyDenied` exception. Its
  `decision.reason_code` is propagated into the `error` trace stage's
  payload; MCP clients see a structured error string. A future revision
  may model this as a return value instead of an exception so the
  reason code reaches the client verbatim.

## Test plan

- `tests/observability/test_traced_tool.py` — decorator semantics,
  sanitization, contextvar isolation, `_truncated` marker.
- `tests/observability/test_traced_tool_policy.py` — read skips policy,
  write/destructive emit policy, RESTRICTED bypass blocked, truthy env
  variants accepted, ComplianceEvent emitted on every decision.
- `tests/store/test_session_store_find_by_hash.py` — repo-scoped lookup,
  cross-repo isolation.
- `tests/hooks/test_git_event.py` — idempotency, agent refresh, no
  duplicate edges, no re-judge on legitimate 0.0.
- `tests/validation/test_pass_rate.py` — threshold > 0 guard, judged
  flag distinguishes from unscored.
- `tests/mcp/test_validation_stats_tool.py` — `repo=None` aggregates
  workspace, `validation_result` stage emitted.

## References

- ADR-006 (explicit restricted-context access).
- ARD-006 (minimum observability).
- dec-107 (validation strategy — extends with aggregate pass rate).
