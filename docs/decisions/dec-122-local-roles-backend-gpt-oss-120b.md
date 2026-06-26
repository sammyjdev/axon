# dec-122 — Back the local roles (scoring, compressor) with gpt-oss-120b, split Groq/Cerebras

- Status: accepted
- Date: 2026-06-26
- Relates to: dec-106 (routing profiles / Ollama opt-in), the scoring role in
  `axon.expansion.scoring`, the caveman compressor in `axon.router.compressor`,
  and the new eval harness `axon.benchmark.model_eval`.

## Context

Two AXON roles run a small instruct model rather than a frontier model:
**scoring** (`expansion/scoring.py`, classifies expansion candidates into a JSON
verdict keep/maybe/discard) and the **caveman compressor**
(`router/compressor.py`, compresses retrieved context while preserving
symbols). Both were pointed at a local Ollama endpoint with hard-coded model
names (`gemma4:e4b` for scoring, `phi3:mini` for the compressor) and no real
evidence that those were the right models.

We evaluated candidates **in task** (not by spec) with a new objective harness,
`axon.benchmark.model_eval`: each model runs the real prompts over gold cases
and is scored on objective checks — `json_valid`, `grounded` (every
`evidence_quote` is literal), `decision_match` (vs gold), `symbols_preserved`
(via `compression_quality`), `compressed`, plus latency. Backends are injected
(`make_ollama_*` / `make_litellm_*`) so the same cases run against local Ollama
or any litellm provider.

Key findings (2026-06-26, n=4 scoring + 3 compressor cases):

| backend / model | scoring (json/grnd/dec) | compressor (sym/comp) | p50 |
| --- | --- | --- | --- |
| **gpt-oss-120b (Cerebras)** | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 | ~0.7s |
| **gpt-oss-120b (Groq)** | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 | 0.8–1.2s |
| gemma4:e4b (desktop Ollama) | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 | 2.7–7.4s |
| qwen3:4b (desktop) | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 | 14–41s (thinking) |
| phi3:mini (desktop, current compressor) | 1.00 / 0.50 / 0.25 | **0.00** / 0.67 | ~2s |

Three findings drove the decision: `phi3:mini` (the current compressor model)
**drops 100% of required symbols**; the spec-favoured `qwen3:4b` matched on
quality but was 2–40× slower (thinking mode); and `gpt-oss-120b` was perfect on
both roles at sub-1.2s on hosted inference. `gemma4` itself is not available on
any reachable hosted provider (Cerebras preview, Groq only Gemma 2; only the 26B
A4B variant is hosted) — the E-series are edge/on-device.

The desktop Ollama path also exposed a latency/OOM trap: scoring never pins
`num_ctx`, so on a host with a large default context (the desktop advertises
262144) the KV cache balloons to multi-GB — OOM on small models, 150s latency.

## Decision

1. Back both local roles with **`gpt-oss-120b`** on hosted inference.
2. **Split the roles across providers** to add the free quotas and avoid
   saturating either: **scoring → Groq** (high RPM — 30 vs Cerebras 5 — for the
   burst of per-candidate requests; the fixed system prompt is cached and does
   not count), **compressor → Cerebras** (high TPM/TPD — 30K/1M vs 8K/200K — for
   larger context payloads). Both keys are permanent (Groq already in the `free`
   profile env; Cerebras key provisioned).
3. Cloud is acceptable for general contexts (the `free` profile already calls
   NIM/Groq); **`ctx=work` stays local/blocked and never goes to a hosted
   provider.**
4. Fall back per handle: provider A → provider B → the existing `anthropic`
   fallback, so the three free quotas stack before any spend.

## Consequences

- Replaces `phi3:mini` for the compressor (a real quality fix, not just eval).
- Removes the hard dependency on the desktop being on / VRAM free.
- Production wiring is **implemented** (`axon.router.llm_backend` +
  `expansion/scoring.py` + `router/compressor.py`): scoring now goes through
  litellm; both roles resolve a full litellm model id (`resolve_litellm_model`)
  and get provider-aware kwargs (`litellm_kwargs` — ollama gets `api_base` +
  `num_ctx`, hosted providers get neither). The dec-106 Ollama gate applies to
  scoring, and the silent `except: pass` is now a logged heuristic fallback.
  Corporate context (`is_corporate_context`) never reaches a hosted provider —
  the compressor falls back to the original text.
- Configuration (env):
  - `AXON_SCORING_MODEL` (default `gemma4:e4b` → `ollama/gemma4:e4b`); set to
    `groq/openai/gpt-oss-120b` for the decided scoring backend.
  - `AXON_CAVEMAN_MODEL` (default `phi3:mini`); set to `cerebras/gpt-oss-120b`
    for the decided compressor backend.
  - `AXON_SCORING_NUM_CTX` (default 8192) and `AXON_CAVEMAN_NUM_CTX` (4096) only
    apply to ollama models. Bare names get an `ollama/` prefix for back-compat.
- Free-tier limits remain a constraint (Groq 30 RPM / 1K RPD / 200K TPD;
  Cerebras 5 RPM / 30K TPM / 1M TPD). The fallback chain absorbs bursts; a Groq/
  Cerebras paid tier is the lever if volume outgrows the split.
