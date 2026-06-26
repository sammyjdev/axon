# MD Chunking Standard — Design

- Date: 2026-06-25
- Status: proposed (sub-project A of "standardize MD + chunks")
- Scope: the Markdown chunking path only (`_chunk_markdown` in
  `src/axon/embedder/chunker.py`). Code chunkers and MD *generation* templates
  (sub-project B) are out of scope.

## Problem

Today `_chunk_markdown` emits **one chunk per heading** (`#`..`######`), splits
only on a fixed `_MAX_CHUNK_LINES = 80` line cap, and has no minimum size, no
small-section merging, and no parent-heading context. Consequences observed on
the live index:

- Markdown dominates the index (2711 of 6911 chunks) with highly uneven sizes —
  a 2-line `######` becomes its own chunk next to an 80-line one.
- Duplicate headings collide: "Custo de tokens" appears 3× in one doc as three
  same-symbol chunks with no disambiguating context.
- A `####` chunk loses its parent (`#`/`##`) context, hurting retrieval.

## Goal

Uniform, context-rich, deterministic MD chunks that improve retrieval, cut
low-value fragments, and read predictably for the LLM — a single quality bar
across the index.

## Market validation (2026)

Structure-aware header splitting is the recommended baseline for structured/MD
content; a Feb-2026 benchmark put recursive 512-token at 69% vs pure semantic at
54%, so semantic chunking would be over-engineering here. Contextual Retrieval
(Anthropic) — prepending context before embedding — cuts retrieval failures ~35%
avg; our heading-path breadcrumb is the cheap, deterministic form of it. Token
band 256–512 is the validated default; overlap of 10–20% is standard on splits.
Sources listed at the end.

## Design

### Strategy: heading-stack + token-band packing

Single pass over the lines maintaining a heading stack `[(level, text), ...]`.
At each heading, pop to `< level`, push the new heading. Every content block
inherits the current heading path. Then pack/merge/split to a token band.

### Parameters

- Token estimate: reuse the existing `_estimate_tokens` (extracted from
  `pipeline.py` into `src/axon/embedder/tokens.py` so chunker and pipeline share
  one source; avoids an import cycle).
- Band: `MIN = 128`, soft `TARGET ≈ 480`, hard `MAX = 512` (matches the
  `bge-base` 512-token embedder window — never exceed).
- Merge: greedily pack consecutive sibling sections toward `TARGET` without
  exceeding `MAX`; never pack across a top-level (`#`) boundary (major sections
  stay distinct). Outcome: no chunk below `MIN` except an unavoidable singleton
  (a lone short doc or an atomic table).
- Split: a section over `MAX` is split by cascade **paragraph → sentence →
  word**, each window `≤ MAX`. Headings always stick to the following block.
- Overlap: **10–15% only between windows of a split section**; none at natural
  heading boundaries (those are already semantic).
- Tables: a Markdown table is atomic — never split mid-row. A standalone table
  over `MAX` is kept whole (documented exception to the cap).

### Breadcrumb (contextual retrieval, lite)

For each chunk build `breadcrumb = "<stem> > <H1> > <H2> > <H3>"` from the
heading path (depth/length capped). It is:

- prepended to `content` (so it is embedded **and** shown in search results), as
  `f"{breadcrumb}\n\n{body}"`;
- used as the chunk `symbol` (with ` > ` separators).

This disambiguates duplicate headings and gives the vector + the LLM the
hierarchical context. Caveat: `start_line`/`end_line` no longer map 1:1 to
`content` because the breadcrumb is synthetic — acceptable.

### Edge cases

- No headings → one section, split by the token band; `symbol = stem`.
- Preamble before the first heading → its own section, `symbol = stem`.
- `#` inside a fenced code block (```` ``` ````) is **not** a heading — fenced
  regions are skipped during heading detection (correctness).
- Duplicate headings → distinct breadcrumbs, so distinct chunks.

### Components touched

- `src/axon/embedder/tokens.py` (new) — `estimate_tokens(text) -> int`.
- `src/axon/embedder/chunker.py` — rewrite `_chunk_markdown`; `chunk_source`
  dispatch and the `Chunk` model are unchanged.
- `src/axon/embedder/pipeline.py` — import `estimate_tokens` from the new util.

### Data flow (unchanged downstream)

`chunk_source(src, "markdown", path)` → new `_chunk_markdown` → `list[Chunk]` →
pipeline embeds `content` (now breadcrumb-prefixed) → pgvector. No schema or MCP
contract change.

## Testing (TDD)

Unit (`tests/embedder/`):

- breadcrumb path is correct across nested headings;
- small sibling sections pack greedily toward `TARGET` so none stay under `MIN`
  (except an unavoidable singleton);
- a section over `MAX` splits at paragraph, then sentence, then word;
- split windows carry 10–15% overlap; heading boundaries carry none;
- a Markdown table is never split mid-row;
- `#` inside a code fence is not treated as a heading;
- duplicate headings produce distinct breadcrumbs/chunks;
- band invariant: every chunk is within `[MIN, MAX]` except documented
  singletons (atomic table, lone short doc);
- no-heading and preamble fallbacks.

Golden: a representative doc (an ADR + `lina/ENGINE.md`) asserting chunk count
and a size histogram within band.

Validation (not unit): re-index the 7 projects; compare MD chunk count and
size distribution before/after; spot-check `search_code` recall on a few MD
queries.

## Migration

Changing the chunker invalidates existing MD chunks. The index does not segregate
by language, so re-index the 7 projects (`pb index-dev`, ~minutes, already
validated on pgvector). Deterministic IDs mean code chunks are rewritten in place;
MD chunks change.

## Out of scope (YAGNI / future)

- Full Anthropic Contextual Retrieval (LLM-generated 50–100 token context per
  chunk) — a future upgrade over the breadcrumb.
- Multi-scale indexing + Reciprocal Rank Fusion.
- Semantic/embedding-based chunking.
- Code chunkers and MD generation templates (sub-project B).

## Sources

- Unstructured — Chunking for RAG best practices: https://unstructured.io/blog/chunking-for-rag-best-practices
- Firecrawl — Best Chunking Strategies for RAG (2026): https://www.firecrawl.dev/blog/best-chunking-strategies-rag
- Anthropic — Contextual Retrieval: https://www.anthropic.com/news/contextual-retrieval
- DEV — Tokenizer-Aware Markdown Chunking That Doesn't Shred Tables: https://dev.to/gabrielanhaia/tokenizer-aware-markdown-chunking-that-doesnt-shred-tables-3kd7
- langcopilot — Document Chunking for RAG: 9 Strategies, Chunk Size & Overlap (2026): https://langcopilot.com/posts/2025-10-11-document-chunking-for-rag-practical-guide
