# dec-106 - Routing profiles: FREE (Groq + NIM) and PAID (OpenRouter + paid Groq)

- Status: accepted
- Date: 2026-05-25

## Context

The AXON router (D2 / ADR-002) defines Haiku/Sonnet/Opus tiers routing directly
to the Anthropic API. Two real-world scenarios are not well served by this:

1. **Modest hardware with no API budget.** A 16 GB Mac cannot run Ollama with
   useful models (gemma4:26b is out of the question; phi3:mini drops in quality).
   Without an API key, AXON is inoperable.
2. **Users with paid credits would prefer OpenRouter** (unified billing, no need
   to manage a separate Anthropic key) while keeping the same Claude tier profile
   that D2 defines.

dec-102 froze router expansion. This decision introduces the concept of
**profile** as configuration - no new modes, no new TOML profiles -
respecting that freeze.

## Decision

Introduce two routing profiles selectable via `AXON_PROVIDER_PROFILE`:

### FREE (default)

No direct cost, subject to provider rate limits.

| TaskType | Model |
| --- | --- |
| TRIVIAL_COMPLETION | `groq/llama-3.1-8b-instant` |
| CODE_ANALYSIS | `groq/llama-3.3-70b-versatile` |
| ARCHITECTURE | `nvidia_nim/meta/llama-3.1-70b-instruct` |
| DEEP_REASONING | `nvidia_nim/meta/llama-3.1-70b-instruct` |
| LOCAL_ONLY | `groq/llama-3.1-8b-instant` |
| UNKNOWN | `groq/llama-3.1-8b-instant` |
| classifier | `groq/llama-3.1-8b-instant` |

### PAID

Paid credit, preserves D2 via OpenRouter.

| TaskType | Model |
| --- | --- |
| TRIVIAL_COMPLETION | `openrouter/anthropic/claude-haiku-4` |
| CODE_ANALYSIS | `openrouter/anthropic/claude-sonnet-4` |
| ARCHITECTURE | `openrouter/anthropic/claude-opus-4` |
| DEEP_REASONING | `openrouter/anthropic/claude-opus-4` |
| LOCAL_ONLY | `openrouter/anthropic/claude-haiku-4` |
| UNKNOWN | `openrouter/anthropic/claude-haiku-4` |
| classifier | `groq/llama-3.1-8b-instant` (cheap) |

### Engine changes

- The `task_type → model` mapping now comes from `axon.router.profiles`, no
  longer hard-coded in `engine.py`.
- Budget downgrade becomes **task-type-driven** (no string comparison of model
  names): ARCHITECTURE/DEEP_REASONING falls back to CODE_ANALYSIS when
  `_OPUS_BUDGET` is exceeded; CODE_ANALYSIS falls back to TRIVIAL_COMPLETION
  when `_BUDGET_USD` is exceeded. Works for any profile.
- `request_opus=True` continues to bypass the Opus budget gate.
- Classifier no longer calls the Ollama SDK directly; always goes through
  LiteLLM with the profile's model.
- OpenRouter compliance validation (`validate_openrouter_compliance`) becomes
  opt-in via `AXON_OPENROUTER_COMPLIANCE=1` (default off). This avoids breaking
  the PAID profile for personal use where compliance metadata does not apply.
  Those who need the original guardrail can re-enable it explicitly.
- `AXON_PROVIDER_OLLAMA` default changes from `1` to `0`. Ollama remains
  supported (ADR-003/D3 intact), but must be enabled explicitly.

## Rationale

- **FREE profile solves the "Mac with no hardware + no API budget" case**
  with decent quality (Llama 70B free on NIM is far better than local phi3:mini).
- **PAID profile preserves D2** semantically - only swaps the transport
  (native Anthropic -> OpenRouter). Does not violate ADR-002.
- **No router surface expansion** (dec-102): profiles are config, not a new
  feature. There is no `axon profile create`, no new modes.
- **Task-type-driven downgrade** is cleaner than string comparison, and scales
  to any future profile without refactoring.

## Consequences

- **ARD-001 (context isolation) reinforced by incompatibility**: FREE/PAID
  profiles are purely cloud. For `ctx=work` (restricted), policy blocks cloud
  - so those tasks fail. **Support for work ctx is out of scope for this
  roadmap** (see Out-of-scope below). Guardrails in `policy/core.py` and the
  `.ctxguard` marker remain active: work ctx stays protected against cloud;
  there is just no execution path.
- **Rate limit gate implemented** in `axon/resilience/rate_limiter.py`
  (fixed-window per minute and per day, Redis with memory fallback).
  Configurable via `AXON_<PROVIDER>_MAX_RPM` / `AXON_<PROVIDER>_MAX_RPD`.
  Defaults: Groq 25/min and 13000/day (margin under real 30/14400), NIM
  50/min and 950/day. When exceeded, `complete()` and classifier raise
  `RuntimeError(DENY_RATE_LIMIT)` - not counted as a model failure (does not
  open the circuit breaker).
- **Cost of the PAID profile does not match native D2 exactly.** OpenRouter
  adds ~10% over direct Anthropic. `_COST_PER_1K` in `profiles.py` reflects
  the OpenRouter estimate, not the native Anthropic price.
- **OpenRouter slugs** (`openrouter/anthropic/claude-{haiku,sonnet,opus}-4`)
  may change when OpenRouter versions them. Override via
  `AXON_PROVIDER_PROFILE` + local editing of `profiles.py` or a future env var.
- **`expansion/service.py` continues using `_RUNTIME.classifier_cloud_model`**
  - now resolved by the profile, so estimated cost may be 0 (Groq free).
  Acceptable.

## Out-of-scope

- **Support for `ctx=work` in this roadmap.** The target hardware (16 GB Mac)
  cannot run Ollama with a useful model, and cloud is blocked by ARD-001.
  Enabling work ctx would require dedicated remote infrastructure (Ollama
  self-hosted on another machine via `AXON_OLLAMA_REMOTE_HOST`), which is
  outside the current focus. Guardrails remain active - tasks with `ctx=work`
  continue to be blocked by policy, which is the correct behavior.
- **Dynamic multi-profile support** (switching profiles mid-session). Profile
  is read at module load time; a restart resolves this.

## Migration

For existing users:

1. No action: default becomes FREE, requires `GROQ_API_KEY` and `NVIDIA_NIM_API_KEY`.
2. To keep native Anthropic D2: setting `AXON_PROVIDER_PROFILE=paid`
   **does not** work for this (PAID uses OpenRouter). Keep exporting
   `ANTHROPIC_API_KEY` and edit `profiles.py` locally to point to native
   slugs (`claude-haiku-4-5-20251001` etc.).
3. If Ollama was configured: set `AXON_PROVIDER_OLLAMA=1` explicitly
   (default changed to 0) and use `AXON_CLASSIFIER_CLOUD_MODEL` pointing
   to `ollama/<model>` (LiteLLM resolves).
