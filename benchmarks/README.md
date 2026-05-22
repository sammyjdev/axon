# AXON Token Savings Benchmark

## What This Is

This is an **explicit deterministic model** of input token cost across a multi-turn coding session, not an instrumented live capture. It models how token consumption grows in two scenarios:

1. **Baseline**: A conventional AI assistant that re-supplies the entire project context on every turn (simulating full context re-send)
2. **AXON**: The same session using AXON's selective context recall to reduce token growth

## Session Assumptions

All runs use the same parameters (defined in `benchmarks/model.py`):

| Parameter | Value | Meaning |
| --- | --- | --- |
| **Session length** | 20 turns | A typical multi-turn coding workflow |
| **Base context** | 1,500 tokens | Initial project context needed for turn 1 |
| **Growth per turn (baseline)** | 300 tokens/turn | Simulates conversation history accumulation in baseline |
| **Recall budget (AXON)** | 2,000 tokens/turn | AXON's fixed `recall_context` budget per turn |

## Modeled Result

```
[baseline] 20-turn session, no AXON: 87,000 input tokens
[axon]     20-turn session, with AXON: 41,500 input tokens
Savings:   52.3%
```

AXON reduces token consumption by **52.3%** in this modeled session.

## How It Works

**Baseline mode:**
- Turn 1: 1,500 tokens (base context)
- Turn 2: 1,500 + 300 = 1,800 tokens (context + accumulated history)
- Turn 3: 1,500 + 600 = 2,100 tokens (context + more history)
- ...
- Turn 20: 1,500 + (300 × 19) = 7,200 tokens

Total: 87,000 tokens

**AXON mode:**
- Turn 1: 1,500 + 2,000 = 3,500 tokens (initial context + first recall)
- Turns 2-20: 2,000 tokens each (fixed recall budget, no history re-send)

Total: 3,500 + (2,000 × 19) = 41,500 tokens

## Honest Caveats

### The >60% Target

The original AXON project plan cited a ">60% token savings" goal. **That was a design target, not a measurement.** This model does not reach it. The model is deliberately conservative:

- It assumes that baseline mode only grows by accumulated decision context (+300 tokens/turn), not full-transcript carryover
- In real-world sessions where LLM assistants re-send entire conversation histories, baseline costs would be much higher, and AXON's savings could plausibly exceed 60%
- However, this benchmark does not assert that — it models only the incremental context growth we can measure

### Model vs. Reality

This is a **model**, not an instrumented capture:
- It does not run actual inference or measure real token consumption
- It assumes deterministic cost growth patterns that real sessions may deviate from
- It does not account for variable compression, token waste from context misses, or other runtime effects

**Use this to understand relative cost trends, not absolute token counts.**

## How to Run

```bash
# Install development dependencies (includes matplotlib)
pip install -e '.[bench]'

# Run the benchmark
make bench
```

Output: A dated PNG chart (e.g., `benchmarks/results/2026-05-22-token-savings.png`) showing cumulative token cost vs. turn number for both modes.

The chart includes the model assumptions in a caption, so it cannot be misread as a live measurement.

## Files

- `benchmarks/model.py` — Core cost model and session parameters
- `benchmarks/visualize.py` — Chart rendering
- `benchmarks/scenarios/long_session_baseline.py` — Baseline scenario runner
- `benchmarks/scenarios/long_session_axon.py` — AXON scenario runner
- `Makefile` — Build target: `make bench`
