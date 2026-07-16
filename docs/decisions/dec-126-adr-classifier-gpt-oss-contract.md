# dec-126: ADR classifier moves to gpt-oss-120b with a 2000-token output contract

**Status:** accepted (2026-07-15)
**Amends:** dec-106 (profile model pins), dec-122 (gpt-oss-120b local roles)

## Decision

The ADR commit classifier (`axon.adr.inference`) changes on two axes,
both measured in a k=3 benchmark grid (7 arms, 10 labeled fixture cases,
`scripts/run_adr_model_grid.py`, `tests/benchmark/fixtures/adr_inference_golden.json`):

1. `classifier_model` in both FREE and PAID profiles:
   `groq/llama-3.1-8b-instant` -> `groq/openai/gpt-oss-120b`.
2. `_call_llm` output contract: `max_tokens` 400 -> 2000.

## Rationale (measured, not vibes)

- Under the old 400-token contract, gpt-oss-120b scored 0/18 valid JSON:
  a reasoning model spends the budget thinking and the JSON truncates.
  At 2000 tokens the same model scores 18/18 JSON and 28/30 correct
  verdicts - statistical parity with `claude -p` (sonnet, plan quota,
  27/30) at ~10x lower latency (~1-2s vs ~14s) and zero cost.
- The previous env-override model (`nvidia_nim/meta/llama-3.3-70b-instruct`)
  ran ~196s/call on NIM's queue the day of the grid (~200x slower than
  Groq) with 16/20 verdicts; the same model on Groq scored 21/30 -
  it systematically invents ADRs for maintenance commits.
- gpt-oss-120b on Groq is the dec-122 precedent (scoring role); this
  extends it to the classifier role with task-specific evidence.

## What n=30 licenses

Parity with the plan-quota Claude arm, not superiority. Failure rate
<= ~10% per check class. The grid's only repeated verdict failure is a
"hard null" (a docs commit describing an architectural direction).

## Follow-ups (explicitly out of this decision)

- Prompt-v2 rule against maintenance-commit over-triggering scored 30/30
  on llama-3.3-70b but was written after seeing those failures. Held-out
  result (8 cases it did not shape, 2026-07-15): 7/8 on llama - it leaked
  on the one null whose prefix (`fix(tests)`) is outside the rule's list,
  i.e. it patches prefixes, not the bias. gpt-oss-120b@2000 scored 8/8
  on the same held-out set WITHOUT the rule. Verdict: rule rejected -
  redundant for the production model, insufficient for llama.
  (`tests/benchmark/fixtures/adr_inference_heldout.json`)
- NIM arms (deepseek-v4-flash, gemma-4-31b, kimi-k2.6) were aborted for
  rail latency, not quality; re-run if NIM queues recover.
- Groq TPM limits break bursts of large prompts (measured); batch callers
  need pacing.
