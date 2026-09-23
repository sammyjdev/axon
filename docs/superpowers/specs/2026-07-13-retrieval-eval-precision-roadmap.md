# Retrieval Eval Precision Roadmap

**Date:** 2026-07-13
**Status:** Approved direction — pending distillation into backlog items / issues
**Provenance:** Gap analysis of the rag-agent-101 course material against AXON,
cross-validated by two independent deep-research reports (Perplexity, Gemini,
2026-07-12). Both validated every item below and every deliberate rejection;
divergences between the two reports are noted inline where they exist.

## Goal

Close the measurement gaps in AXON's retrieval benchmark before adopting any
new retrieval technique. This spec extends (does not replace) the
benchmark-guided evolution design
(`2026-07-11-benchmark-guided-axon-evolution-design.md`) and inherits its
promotion rule: no retrieval technique lands without a reproduced, isolated
benchmark loss.

## Scope boundary

AXON-side items only. The FORGE-side counterpart of this validated plan lives
in the forge repo (`agents/forge/docs/loop-quality-roadmap.md` in
claude-skills).

## Item PREC-1: Context precision metric in `retrieval_eval`

The only immediately actionable item. Highest priority of the whole plan.

**Problem.** `retrieval_eval.py` reports recall-side metrics only
(`recall_first`, `recall_after`, `delta`, `retry_rate`, `give_up_rate`).
There is no signal for noise: what fraction of returned chunks is actually
relevant. Every irrelevant chunk burns the agent context budget that is
AXON's core value proposition, and a precision regression (e.g. the lexical
RRF arm pulling false positives) is invisible today.

**Direction.**
- Add a precision counterpart to recall@k in `evaluate()`, computed from the
  same golden fixture (`tests/benchmark/fixtures/retrieval_golden.json`) —
  relevance labels already exist; no new harness, no new corpus.
- Report precision alongside recall in the same output; both before and after
  self-correction, mirroring the recall_first/recall_after split.
- Effort: S.

**Validation notes.** Both reports rank this first. Industry framing:
context_precision is the standard companion to context_recall (RAGAS);
recall-only evaluation misses precision regressions entirely.

## Deferred items (gated by the promotion rule)

These are recorded so they are not re-litigated. Each stays out until its
named trigger fires.

### PREC-2: Query-class breakdown (= Level 2 of the 2026-07-11 spec)

Already specified there ("Hermetic Reporting by Query Class"). The course and
both reports confirm the priority and the taxonomy (exact symbol / file path /
structural relation / NL code query maps cleanly onto the industry
text-to-code / code-to-code / lexical / structural split). Trigger: the first
retrieval change that needs isolation — unchanged from the parent spec.

### PREC-3: Code-tuned embedding benchmark spike

The two reports diverge here. Perplexity: benchmark code-tuned embeddings
(voyage-code-3 class) proactively — published deltas are large. Gemini: keep
bge-m3 — hybrid + cross-encoder rerank neutralizes most of the delta on a
small corpus, and code-tuned options are closed paid APIs (conflicts with the
FREE profile and local-first posture, dec-106).

Resolution: side with Gemini for adoption, concede Perplexity a cheap
experiment. After PREC-2 exists, IF the dense arm underperforms on the
"exact symbol" or "structural" classes (lexical arm carrying them), run a
benchmark-only spike: embed the golden set with one code-tuned model and
compare per-class recall/precision. No migration, no production wiring —
numbers first. Trigger: PREC-2 data showing a dense-arm loss.

### PREC-4: Repo-map artifact (PageRank over the symbol graph)

The only genuinely new idea from the deep-research round (Gemini). AXON
already stores the dependency graph in Postgres (`symbol_deps`); the gap is a
compressed orientation artifact (Aider-style repo map, PageRank-ranked
definitions) served to agents. Plausibly high impact for structural queries,
but effort M/L and unproven for AXON's corpus. Trigger: PREC-2 shows the
"structural relation" class losing, and the graph fallback in
`self_correct.py` does not already cover it.

Ownership note (2026-07-13 GLYPH gap analysis): GLYPH already ships tested
PageRank at the `GraphStore` port (`networkx_store.pagerank()`,
`neo4j_store.pagerank()`, `GraphRetriever(pagerank_weight=...)`). If PREC-4
triggers, do NOT reimplement PageRank over `symbol_deps` — consume GLYPH's
ranking primitive (or match it as the reference implementation); only the
artifact-shaping (compressed repo map) belongs in AXON.

## Rejections re-confirmed (do not revisit without new evidence)

Both reports validated all seven standing rejections: HyDE, multi-query
retrieval, semantic chunking, dedicated vector DB, in-process RAGAS,
per-query agentic retrieval, dollar-level cost tracking. Two nuances worth
recording:

- **Online eval sampling** (Perplexity's "missing practice"): largely already
  covered by the per-chunk recall telemetry sidecar (dec-553) and recall
  savings reports (dec-561/565). No new LLM-judge sampling subsystem; at most
  a periodic drift check reading the existing sidecar, if ever needed.
- **Query routing classic/agentic** (Perplexity): the embryo already exists —
  `self_correct.py` routes structural queries to the graph fallback. PREC-2
  data decides whether more is needed.

## Ordering

PREC-1 is independent and ready. PREC-2 stays on its parent-spec trigger.
PREC-3 and PREC-4 are strictly behind PREC-2. Nothing here blocks or reorders
the existing L1/OPS/HTTP/MS/EMB backlog.
