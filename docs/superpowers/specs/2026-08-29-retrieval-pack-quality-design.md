# Design: pack quality — measure it, then let the reranker pick

- Date: 2026-08-29
- Relates to: dec-131 (ctx partitions retrieval), issue #45 (NL → code recall),
  dec-119 (canonical activity stores)

## Problem

Retrieval returns eight segments and nothing checks whether they are the right
eight. The investigation behind dec-131 removed one cause — a non-protected ctx
partitioned the search — but the remaining question was never measurable: is the
selection any good?

Measured on 2026-08-29, using each `axon` decision's `summary` as the question
and its `files` as the answer key (60 cases, `top_k=8`, the `balanced` strategy
in production):

Two sample sizes appear in this document and they are not interchangeable:
the table below is n=60, the reranker table in section 4 is n=40 (the reranker
runs were slower and used a smaller draw from the same query). Compare rows
within a table, never across.

| | brings at least one right file | mean file coverage |
|---|---|---|
| top-8, as shipped | 35/60 = 0.583 | 0.384 |
| top-24 | 43/60 = 0.717 | 0.538 |

**Four questions in ten build a pack containing none of the files that the work
actually touched.** More candidates help; that is the whole signal.

## What was ruled out first

- **Chunk size.** Bigger blocks dilute the vector — an embedding of more subject
  matter matches every question equally badly. The measurement says bring *more
  blocks*, not *bigger* ones. dec-131 already showed that large material without
  better selection is only more to compress.
- **The ANN index.** `get_dice_roll_request` is the exact rank-1 answer (0.553)
  and HNSW at the default `ef_search=40` drops it from the top-5 entirely.
  Real, reproducible, and worth exactly one case on the 24-case golden set
  (18/24 → 19/24). Not the systematic cause.
- **A better embedding model.** Premature. Nothing here is decidable before a
  ruler exists.

## Decision

Build the ruler, remove the waste, then let the existing reranker choose.
Nothing about chunking, embeddings, or index parameters changes.

### 1. Pack-quality eval

`scripts/eval_pack_quality.py`, reporting two numbers: the fraction of cases
whose pack contains at least one expected file, and mean per-case file coverage.

Cases are derived from decisions — 812 of 860 carry a non-empty `files` list and
every one carries a `summary`, so the ground truth already exists and is 34×
the size of the current golden set.

**The case list is generated once into a committed fixture, never queried live.**
A ruler that reads the database grows new cases every week and silently stops
being comparable to last week's run — the same failure as a metric filtered on
`reduction_pct > 0`, which selects the events that worked and then reports that
everything worked.

The existing `scripts/eval_nl_code_recall.py` stays as a regression guard on
symbol recall (18/24 today, against the 6/24 baseline in #45).

### 2. Record the query

Compression and recall telemetry record `before_tokens`, `ctx` and `caller`, but
never what was asked, so no real case can be reconstructed. Record the query
alongside the event so a later ruler can be built from real traffic instead of
from decisions.

**Except when `ctx=work`.** The isolation dec-131 deliberately preserved must
not leak through a telemetry file.

### 3. Dedup and diversify the pack

Drop byte-identical content and cap chunks at 2 per file before assembly.
Measured waste over 40 questions: 10.0% of slots carry byte-identical content
and 13.4% repeat a file. The cap is 2 rather than 1 because a large file
legitimately answers a question in more than one place, and 13.4% is repetition,
not a flood. Small on its own — its purpose is to free the slots section 4
wants.

### 4. Enable the reranker

`jinaai/jina-reranker-v2-base-multilingual` already exists in `_rerank_hits`,
gated behind `AXON_RERANK=1`, and has never been measured. It works: it scores
`delete_by_file` at -1.31 against -3.74 for an unrelated test, a separation of
2.4 where cosine separated the same pair by 0.02.

Measured over 40 cases:

| arrangement | hit rate | coverage | latency | pack slots |
|---|---|---|---|---|
| top-8, as shipped | 0.525 | 0.298 | ~0 | 8 |
| top-24, no rerank | 0.675 | 0.445 | ~0 | 24 |
| 24 cand × 1200 chars → 8 | 0.675 | 0.418 | 9.37s | 8 |
| 24 cand × 400 chars → 8 | 0.675 | 0.405 | 2.69s | 8 |
| **48 cand × 400 chars → 8** | **0.700** | **0.438** | **4.61s** | **8** |
| 48 cand × 1200 chars → 8 | 0.750 | 0.493 | ~20-30s | 8 |
| 48 cand × 256 chars → 8 | 0.650 | 0.412 | 3.16s | 8 |

The reranker delivers in 8 slots what raw search needs 24 slots to deliver.
**48 candidates × 400 chars is the knee**: 1200 chars buys +0.05 hit rate for
+20 seconds, and 256 chars degrades, so 400 is a real optimum rather than
"shorter is better".

Changes: `_RERANK_CANDIDATES` 24 → 48, `_RERANK_TEXT_CHARS` 1200 → 400, and
rerank on by default (`AXON_RERANK=0` opts out).

Three conditions, without which this must not ship on by default:

- **Load with a timeout.** `_get_reranker()` calls `TextCrossEncoder(model)` with
  no bound. A stalled model download hangs the MCP server rather than degrading
  it, and `except Exception` does not catch a hang. This machine has a recorded
  `hf_xet` TLS failure that hangs HF downloads forever; the model only downloaded
  under `HF_HUB_DISABLE_XET=1`, which appears nowhere in the codebase.
- **Set `HF_HUB_DISABLE_XET=1` at the load site**, so the first call on a cold
  cache completes instead of hanging.
- **Fail loud.** `_rerank_hits` currently catches every exception and returns the
  original order, producing a pack indistinguishable from a reranked one. Record
  whether the rerank ran. This is the defect that kept the judge dead for months
  and a telemetry series stopped for 38 days: a thing that never ran looked
  exactly like a thing that succeeded.

English-only rerankers were rejected despite being 6-11× faster
(`ms-marco-MiniLM-L-12` at 1.97s, `jina-reranker-v1-turbo-en` at 1.08s): the
real workload is a Portuguese question against English code, which is precisely
where multilingual pays.

## Order

1. Eval + fixture — nothing below is verifiable without it
2. Query recording (independent, small)
3. Dedup
4. Reranker

Each step re-runs the eval. A step that does not move it gets reverted, not
explained.

## Testing

TDD per step:

- eval scores a known-good and a known-bad pack correctly, and reads the fixture
  rather than the database
- `ctx=work` never reaches the telemetry record
- dedup drops identical content, caps per file, preserves relative order
- a failing rerank marks telemetry instead of silently returning the input order
- a load timeout returns unreranked hits instead of hanging

## Open questions

- Exact search scores 18/24 on the golden set while `ef_search=200` scores
  19/24. Exact should be a ceiling, not a floor. Unexplained; ±1 case, so it
  does not block this work, but it means the golden set has instability that
  should not be read as signal.
- Searching four collections (dec-131) costs one case on that same golden set
  (18 → 17). The fixture is biased toward partitioning — every case was written
  with its correct ctx already supplied, which is what real use does not have —
  but the number stands and the pack-quality eval should be watched for the same
  effect.
