# dec-127: AWS Bedrock generation backend through the existing litellm funnel

**Status:** accepted (2026-07-25)
**Relates to:** dec-106 (provider profiles), D2 (task-based routing)

## Context

AXON's generation providers (anthropic, openrouter, groq, nvidia_nim, ollama)
all route through one funnel: `complete_with_usage` -> `_call_completion` ->
`litellm.acompletion`, with circuit breaker, rate limiting, daily budget and
usage capture attached to that single path. We want a managed-enterprise
generation option (AWS Bedrock, Converse API) without weakening that design.

Two shapes were considered:

1. **Standalone boto3 adapter module** — a parallel generation path calling
   `bedrock-runtime.converse()` directly. More visible Bedrock code, but it
   bypasses breaker, rate limit, budget and usage capture, or duplicates them.
2. **Provider wiring in the existing funnel** — `bedrock/` model prefix in
   `provider_for_model`, opt-in flag, litellm drives the Converse API through
   boto3 underneath.

## Decision

Option 2. Bedrock is a provider prefix, not a parallel path:

- `provider_for_model("bedrock/...")` -> `"bedrock"`.
- Opt-in via `AXON_PROVIDER_BEDROCK=1` (default off), same pattern as D3's
  opt-in Ollama. Disabled requests fail with `provider disabled: bedrock`.
- AWS credentials are never AXON config. AXON carries **names only**:
  `AXON_BEDROCK_PROFILE` (boto3 named profile; unset falls back to the
  default boto3 chain) and `AXON_BEDROCK_REGION` (default `us-east-1`).
  AXON-scoped envs keep the machine-global `AWS_PROFILE` untouched.
- The provider availability string (prompt-layer cache key) includes bedrock
  so toggling the provider cannot serve stale system layers.
- `boto3` ships as the optional extra `axon-context-mcp[bedrock]`; the
  default install stays boto3-free.
- Bedrock is **not** in the D2 profile tiers. It is reachable via the pinned
  model escape hatch (`AXON_COMPLETION_MODEL=bedrock/<inference-profile-id>`)
  or future profile revisions. Note Bedrock invokes recent Anthropic models
  by **inference profile ID** (`us.anthropic....`), not the bare model ID.
- `scripts/bedrock_smoke.py` is the raw boto3 Converse check, so a live
  failure can be attributed to AWS setup vs AXON wiring.

## Justification

The funnel owns resilience. A second generation path would either bypass the
breaker/rate/budget/usage machinery or duplicate it — both are architectural
regressions to gain nothing: litellm already speaks the Converse API. The
rate limiter picks up `AXON_BEDROCK_MAX_RPM/RPD` with zero new code because
`spec_from_env` derives env names from the provider string.

## Consequences

- Verified live 2026-07-25: `bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0`
  through the full funnel (routing, breaker, usage capture: 111 in / 115 out
  tokens) and through the raw smoke script, using a dedicated least-privilege
  IAM user (`bedrock:InvokeModel*` + Marketplace subscribe) under a $2 budget
  alarm.
- Cost governance: `_COST_PER_1K` has no Bedrock entries yet, so projected
  cost treats Bedrock as 0 in budget pre-flight. Acceptable while Bedrock is
  pinned-model-only; must be added if a profile ever routes to it.
- Fallback on Bedrock failure follows the existing task-type fallback (D2:
  trivial of the active profile), so a Bedrock outage degrades to the
  configured free-tier provider automatically.
