# dec-118 — Keep rtkx for reversible compression; use LLMLingua-2 as the lossy benchmark baseline

- Status: accepted
- Date: 2026-06-20
- Relates to: dec-116/dec-117 (narrow-and-deep, swappable stack layers), the
  compression telemetry in `data/compression/stats.jsonl` and
  `axon.observability.compression_telemetry`.

## Context

rtkx (`sammyjdev/rtkx`) is AXON's compression tier: it shrinks context windows
and keeps the pre-compression original retrievable (the reversible CCR-style
store behind `restore_context` / `AXON_RTK_REVERSIBLE`). AXON currently reports
local compression telemetry (p50 85.5%, mean 78.8% over n=69 windows) but with
**no comparable baseline** — the numbers stand alone, which is weak evidence
next to competitors that publish quality-retention benchmarks.

[LLMLingua-2 / LongLLMLingua](https://github.com/microsoft/LLMLingua)
(Microsoft) is the reference open-source prompt-compression library. It is a
**different approach** from rtkx: a small model classifies/scores tokens by
perplexity and drops the low-information ones. That makes it **lossy and not
trivially reversible** — the dropped tokens are gone, there is no original to
restore. It is task-agnostic and fast (LLMLingua-2 is 3–6× faster than v1).

So rtkx and LLMLingua are not swaps:

| | rtkx | LLMLingua-2 |
| --- | --- | --- |
| Mechanism | Structural compression + reversible store | Perplexity-based token dropping |
| Reversible | Yes (CCR original retained) | No (lossy, dropped tokens gone) |
| Cost at compress time | Local binary, deterministic | Small LM inference per window |
| Fit for AXON | `restore_context` requires the original | Would break reversibility |

## Decision

1. **Keep rtkx as the compression tier.** Reversibility is a load-bearing
   feature of AXON (`restore_context`, dec stack). A lossy compressor that
   discards the original cannot back that contract, so LLMLingua-2 is not a
   candidate to *replace* rtkx.

2. **Adopt LLMLingua-2 as a measurement baseline, not a dependency.** Add it to
   the compression benchmark harness as the reference lossy compressor, so AXON
   can report rtkx's ratio *and* quality retention against a recognised number
   instead of a standalone figure. This closes the credibility gap (competitors
   publish quality-retention benchmarks; AXON publishes a ratio with no
   comparator) at the cost of a dev-only/eval-only dependency — it never enters
   the runtime path.

3. **Do not ship LLMLingua in the runtime.** It stays behind the benchmark
   extra, mirroring how GLYPH's heavy `embeddings` extra is intentionally
   omitted (dec-116). The shipped compression path remains rtkx-only.

This is the dec-115/dec-117 pattern again: borrow the external project as a
reference/benchmark, keep the architecture and the dependency surface lean.

## Consequences

- A new benchmark comparator (rtkx vs LLMLingua-2 at matched ratios) is a
  follow-up, test-first per the repo rules; this record does not add a runtime
  dependency or change the compression pipeline.
- The token-savings story in the README/METRICS can move from a standalone
  ratio to a ratio-plus-retention figure once the comparator lands.

## Open follow-ups

- Define the quality-retention metric for the comparator (e.g. downstream task
  accuracy or answer-equivalence) before wiring LLMLingua-2 in.
- Decide whether the comparator runs in CI or only on demand (LLMLingua-2 pulls
  a model; likely on-demand, consistent with dec-104's no-idle-cost stance).
