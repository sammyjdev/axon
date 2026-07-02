# Design — Self-correcting retrieval in `ask()`

Date: 2026-07-01
Status: Draft (brainstorming output, pre-plan)

## Problem

`ask()` (`src/axon/mcp/server.py:548`) routes context (via `ContextDetector`) and
calls `_retrieve_context` **once**. It never judges whether the retrieved context
is sufficient, and never re-tries with another strategy before answering. This is
the gap the corrective-RAG / self-RAG literature closes: grade the retrieval,
recover from a bad one, then answer.

This design adds a **self-correcting retrieval layer**: one bounded correction
step between retrieval and compression, always on, with a kill-switch.

Note: this is **not** "add RAG to AXON". Multi-project RAG already exists and is in
production. This adds the *self-correction loop* that RAG lacks today.

## Non-goals

- No generation-quality / hallucination scoring — `ask()` returns *context*, not a
  final answer. Faithfulness eval is out of scope.
- No `ctx` switching during retry (keeps the restricted-context gate dec-109 intact).
- No parameter-widening rung (lowering similarity threshold trades relevance for
  noise; not worth the knob).
- No new MCP tool — the loop lives inside `ask()`.
- No dedicated trained relevance grader (CRAG-style T5). Deliberately deferred; see
  Market Validation.

## Decisions

### D-A: Insufficiency signal is a hybrid cascade

A three-band cascade over the retrieval `hits`, from cheapest to most expensive:

1. **Cheap rung** — `hits` empty **or** aggregate similarity score `< LOW` →
   insufficient. No LLM call.
2. **Gray zone** — aggregate score in `[LOW, HIGH)` → call `judge_fn` (a cheap LLM
   through the same `_POLICY` gateway `ask()` already uses, FREE profile) with the
   query + retrieved context → verdict sufficient/insufficient.
3. **Confident rung** — score `>= HIGH` → sufficient, no judge call.

`LOW` and `HIGH` are **calibrated against the golden set** (D-E), not pre-fixed.

Assumption to verify in planning: `hits` carry an aggregatable similarity score.
If not, the gray-zone judge becomes the only signal and the cheap rung reduces to
the empty-hits check.

### D-B: One retry, strategy chosen by query shape

If insufficient, classify the query and pick **exactly one** strategy:

- **Structural query** (mentions a dependency / "quem usa" / "depende de" / a
  token that looks like a symbol) → **graph fallback**: `get_graph_neighbors` /
  `get_graph_path` seeded from the symbols of the best hits.
- **Otherwise** → **query reformulation**: a cheap LLM rewrites the query;
  re-run `search_code`.

Re-judge the new result with the cheap rung only. No second retry.

### D-C: One retry cap, then honest give-up

Hard cap of 1 retry (2 attempts total). If still insufficient, `ask()` returns the
context it has, prefixed with an honest header
(`⚠ contexto recuperado pode ser insuficiente para esta query`). It does not
fabricate and does not loop. This is the guaranteed-termination / force-generate
step the agentic-RAG loop requires; the hard cap structurally prevents the known
`retrieve→grade→rewrite` infinite-loop bug class.

### D-D: Internal to `ask()`, extracted module, kill-switch

The loop is a pure function `correct_retrieval(query, ctx, hits, pack,
retrieve_fn, judge_fn) -> CorrectionResult` in a new module
`src/axon/retrieval/self_correct.py`. `ask()` calls it between `_retrieve_context`
and compression. Env kill-switch `AXON_SELF_CORRECT` (default on) disables the loop
without redeploy for incident response.

`correct_retrieval` requires `self_correct.py`. `ask()` requires `correct_retrieval`.

### D-E: New retrieval benchmark

New `src/axon/benchmark/retrieval_eval.py` + a golden set (query → expected
symbols/docs + ctx). Metrics:

- **recall@k first-try vs recall@k after-correction** — the delta is the value of
  the loop.
- retry-rate, give-up-rate.
- judge precision — did the judge flag insufficient when retrieval was actually bad.

`retrieval_eval.py` does **not** reuse `model_eval.py`: `model_eval.py` compares
*models* (scoring/compressor across Ollama vs litellm), not retrieval quality.

## Architecture

```
ask(query, ctx):
    detect ctx (ContextDetector)          # unchanged
    hits, pack = _retrieve_context(...)   # unchanged
    if AXON_SELF_CORRECT:
        result = correct_retrieval(query, ctx, hits, pack,
                                   retrieve_fn=_retrieve_context,
                                   judge_fn=<cheap LLM via _POLICY>)
        hits, pack, meta = result.hits, result.pack, result.meta
        trace.append_stage("self_correct", payload=meta)
    ... compression (unchanged) over pack ...
```

`correct_retrieval` is pure: retrieval and judging are injected, so it is tested
without booting the MCP server or hitting a live LLM.

### Data flow

1. `ask()` → `_retrieve_context` → `hits`, `pack`.
2. `correct_retrieval` grades `hits` (cascade D-A).
3. Sufficient → returns unchanged `hits`/`pack`, `meta.retried=False`.
4. Insufficient → picks strategy (D-B), calls `retrieve_fn` once more, re-grades.
5. Still insufficient → give-up header, `meta.gave_up=True`.

### Observability

New trace stage `self_correct` via the existing `trace.append_stage`, fields:
`verdict`, `strategy_used`, `retried`, `gave_up`. Feeds telemetry and the eval.

## Market Validation (CRAG / Self-RAG / agentic-RAG loop)

This design is a cost-optimized instance of the established pattern.

- **D-A relates to CRAG.** CRAG's retrieval evaluator emits three bands
  `{Correct, Ambiguous, Incorrect}`; our cascade is the same shape
  (`HIGH` / gray / `LOW`). Divergence: CRAG runs a dedicated grader (T5, 0.77B) on
  *every* query; we trust the raw similarity score at the extremes and judge only
  the gray zone. Cheaper, but similarity is a weak relevance proxy ("confident and
  wrong"). Mitigation: calibrate `LOW`/`HIGH` on the golden set; widen the gray
  zone if the score proves a poor proxy. Deliberately deferred: a dedicated grader.
- **D-B relates to CRAG and the agentic loop.** Reformulation = the agentic loop's
  query-rewrite; graph fallback = CRAG's "resort to a complementary source" (code
  graph instead of web). Divergence: CRAG's Ambiguous action *combines* refine +
  external; we pick one strategy (1-retry budget).
- **D-C relates to the agentic loop.** Reflexion uses `MAX_ITERATIONS=3`, LangGraph
  typically 2; we use 1 (low end, same family). The force-generate / guaranteed
  termination the loop requires is our honest give-up; the hard cap prevents the
  known infinite-loop bug.
- **D-D is orthogonal.** Self-RAG's retrieve-on-demand (skip retrieval) does not
  apply — `ask()` is a retrieval tool.
- **D-E** measures retrieval recall (our surface), not generation hallucination
  (Self-RAG's 5.8% metric), which needs a final-answer eval we don't have here.

## Relations

- Relates to: dec-122 (`model_eval.py` — model comparison, distinct from D-E's
  retrieval eval).
- Relates to: dec-109 (restricted-context gate — D-D/D-B never switch `ctx`).
- Relates to: dec-115 (soft supersession — orthogonal ranking concern).
- Requires: `_retrieve_context`, `_POLICY` gateway, `ContextDetector` (existing).

## Open items for planning

1. Verify `hits` carry an aggregatable similarity score (D-A assumption).
2. Choose the cheap judge model id under the FREE profile and confirm it routes via
   `_POLICY`.
3. Define the query-shape classifier for D-B (structural vs not) — keyword/heuristic
   first, no model.
4. Author the golden set for D-E (size, per-ctx coverage).
5. Calibrate initial `LOW`/`HIGH` from the golden set.
