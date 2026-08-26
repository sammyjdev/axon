# dec-128 - Budget profile model substitution

- Status: accepted
- Date: 2026-08-26

## Context

On 2026-08-26, real litellm chat completions confirmed that several models in the budget profile (formerly FREE) were dead:
- `groq/llama-3.1-8b-instant` and `groq/llama-3.3-70b-versatile` returned `model_not_found` (Groq dropped the llama family).
- `nvidia_nim/meta/llama-3.1-70b-instruct` returned HTTP 410 Gone (end of life).

The consequence was that `score_decision()` in `src/axon/validation/judge.py` caught the provider error, logged "decision judge unavailable, score skipped", and returned None. Consequently, every decision landed with `judged: false`. As per RULES.md and dec-121, `judged` is the canonical flag; `validation_score == 0.0` is not a sentinel.

Substitution options:
- Groq survivors are reasoning models that use a separate `reasoning` field or emit `<think>` tags. The judge parses the first number from `content`, so this would be a parsing-contract change.
- NVIDIA NIM and Cerebras were rejected: NVIDIA NIM's catalogue has 83 entries and lists at least two it does not serve: `baai/bge-m3` and `mistralai/mistral-7b-instruct-v0.3` both return 404 `Not found for account`. That is issue #143. The lesson recorded here is the one the "Done when" of #154 states: a catalogue listing is not evidence, only a real call is. Cerebras lists two models and bills for both - a probe returns `Payment required` - so it is not an option for this profile at all, not merely for its Llama variants.

The four verified-alive ids were probed:

| provider | id | latency | reply |
| --- | --- | --- | --- |
| DeepInfra | `meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo` | 1702ms | `'4'`, 2 completion tokens, no reasoning field |
| DeepInfra | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | 345ms | `'4'`, 2 completion tokens, no reasoning field |
| OpenRouter | `meta-llama/llama-3.1-8b-instruct` | 1220ms | `'4'`, 2 completion tokens, no reasoning field |
| OpenRouter | `meta-llama/llama-3.3-70b-instruct` | 1026ms | `'4'`, 2 completion tokens, no reasoning field |

Note that `openrouter/meta-llama/llama-3.1-8b-instruct` was verified but is NOT routed by any tier in the final ladder, so it deliberately carries no `cost_per_1k` entry: a price for a model nothing routes to is dead data.

Only `gpt-oss-120b` and `gpt-oss-20b` exist across all four accessible catalogues and both are reasoning models. Thus, two providers with an unchanged calling contract were preferred over four with a new one.

## Decision

1. Define a new `budget` profile and keep `free` as an alias.
2. Implement a tier ladder for the budget profile:
   - TRIVIAL_COMPLETION: `deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo`
   - CODE_ANALYSIS: `openrouter/meta-llama/llama-3.3-70b-instruct`
   - ARCHITECTURE/DEEP_REASONING: `deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo`

The mid rung is on OpenRouter to satisfy the invariant (issue #100) that every profile must have three pairwise-distinct tier rungs. This ensures that a top-tier failover (`_fallback_model_for`) results in a cross-provider downgrade (DeepInfra -> OpenRouter).

3. Update provider plumbing:
   - `provider_for_model()` now returns "deepinfra" for `deepinfra/` prefixes. This enables the use of `AXON_DEEPINFRA_MAX_RPM` / `AXON_DEEPINFRA_MAX_RPD`.
   - `llm_backend._KNOWN_PROVIDERS` updated to prevent `resolve_litellm_model()` from prefixing these with `ollama/`.
   - `deepinfra` is deliberately absent from `engine.py`'s `provider_enabled` dict, so it defaults to enabled - the same precedent
     already set for `groq` and `nvidia_nim` - and that `AXON_DEEPINFRA_MAX_RPM` / `AXON_DEEPINFRA_MAX_RPD` are unset by default, i.e. uncapped, matching those same providers.
   - Before this change `provider_for_model()` fell through to `"anthropic"` for an unknown prefix, so a `deepinfra/` id would have been gated behind `provider_anthropic_enabled`,
     run through `validate_anthropic_cache_control`, and rate-limited under `AXON_ANTHROPIC_MAX_RPM`.

4. Update pricing based on 2026-08-26 data from DeepInfra (`/models/list`) and OpenRouter (`/api/v1/models`).
   Stored values are max(input, output) per 1K tokens to avoid budget underestimation.
   - 8B (DeepInfra): max(2e-06, 4e-06) c/tok * 10 = 4e-05
   - 70B (DeepInfra): max(1e-05, 3.2e-05) c/tok * 10 = 3.2e-04
   - 70B (OpenRouter): max(7.1e-07, 7.1e-07) * 1000 = 7.1e-04
   553 decisions in the busiest month, a judge prompt of roughly 400 tokens, at the most expensive routed rate (7.1e-04 USD per 1K) is about 0.16 USD per month, and at the DeepInfra 70B rate about 0.07 USD per month. Cost was therefore not the deciding factor; calling-contract stability was.

5. Rename `free` to `budget` in the registry, but retain `free` as a key for compatibility with `src/axon/config/runtime.py` defaults and the CI matrix.

## Unchanged on purpose

- `src/axon/validation/judge.py`: untouched. The judge was never broken; it was correctly
  reporting that no model would answer. Repointing the profile fixes it with no parser change,
  which was the whole point of preferring dense models.
- `classifier_model` on BOTH profiles stays `groq/openai/gpt-oss-120b`, verified alive on
  2026-08-26 returning `content='CODE_ANALYSIS'` in 604ms under the classifier's real call
  shape (`max_tokens=32`, `reasoning_effort="low"`). It is a reasoning model, and it only
  empties `content` when `reasoning_effort` is omitted - dec-126 already closed that and
  stands unchanged. Groq therefore remains a required key even though no tier routes to it.
- `src/axon/router/engine.py`: untouched. Keeping three pairwise-distinct tier rungs meant the
  existing `_fallback_model_for` tier downgrade already produces the cross-provider failover;
  an explicit per-task fallback map was drafted, tried, and dropped because it would have
  overridden the tier contract that `tests/router/test_engine_completion_fallback.py` pins.
- `src/axon/config/runtime.py`: untouched. Its hardcoded `"free"` default keeps validating
  because `free` stays a registry key.

## Rationale

The tier ladder provides the necessary failover distinction without requiring changes to `engine.py` logic. The renaming to `budget` reflects that these models now carry a small cost, while the alias prevents breaking the CI and runtime defaults.

## Consequences

- CI does not make real provider calls; therefore, future provider-side id removals will result in green tests but broken production.
- Regression proxy: the frozen allowlist in `tests/router/test_profiles_budget.py`.
- Remedy: repeat manual probing.

Follow-up: migrate the `free` call sites (runtime.py default, the CI matrix `profile: [free, paid]` and its derived job names, and the 8 test files) to `budget`; the alias stays until then.
