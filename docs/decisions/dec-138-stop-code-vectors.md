# dec-138: stop vectorizing code; no commit and no MCP tool calls an LLM

- Status: Accepted
- Date: 2026-10-15
- Supersedes: dec-106 (routing profiles), dec-116 (GLYPH retrieval), dec-122
  (local roles backend), dec-126, dec-127, dec-128, dec-131, dec-132, dec-133;
  ADR-002, ADR-003, ADR-004, ADR-005 and ADR-010 in `docs/ADR.md`
- Relates to: dec-110 (commit signal), dec-136 (activity history)

## Decision

AXON no longer indexes or embeds code. It becomes the memory of the work done
with agents: decisions, lessons, sessions and, next, the conversations.

Removed, with their CLI commands, MCP tools, doctor checks, scripts and tests:

- the code index: `embedder` (chunkers, pipeline, sync, providers, fastembed),
  `code`, `retrieval`, the search and pack parts of `context`, the pgvector
  store, the file cache, the `symbol_deps` and node/edge graph stores, GLYPH;
- `search_code`, `ask`, `get_dependencies`, `get_graph_neighbors`,
  `get_graph_path`, `get_graph_context`, and `axon_export_now` /
  `axon_mark_done` (zero calls over the whole trace);
- `axon search`, `sync`, `index-dev`, `index-vault`, `index-compare`,
  `index-prune-legacy`, `scan`, `ingest-vault`, `graph {index,neighbors,path}`;
- ADR inference by LLM and its gates, the ADR draft pool and its commands
  (`adr infer-commit|review|audit|validate-drafts|hook`);
- the `router` and `resilience` packages, the caveman compressor and the
  OpenAI-compatible `/v1/chat/completions` endpoint that served gnomon-eval;
- the `axon setup` question about languages and usage profile, whose answers
  nothing read.

PR #225 had already removed `benchmark`, `expansion`, `watcher`, `registry`,
`domains`, `pet`, `portability`, the push-time LLM judge and the LLM session
summary.

What changed instead of going:

- A commit is captured only when it carries a signal: an `arch:` / `decision:`
  subject or an `ADR-Decision:` trailer becomes a draft Decision, a `Lesson:`
  trailer becomes a lesson draft. A plain commit touches nothing. `post-merge`
  captures signal commits a pull brought in; `post-checkout` is retired and
  reinstalling the hooks strips its block, as it already did for `pre-push`.
- `axon_search_lessons` is Postgres full-text search (English stemming, triggers
  weighted over mistake, tell and fix): no embedding and no provider key.
- `axon_health` probes Postgres instead of pgvector.

The `embeddings` table is dropped after this ships, with a `pg_dump` first. The
other tables stay.

## Why

Measured on 2026-10-15 (`.specs/features/memory-direction/analysis.md`):

1. `search_code` drew 199 calls in 5.5 months, 80 of them errors. In 74
   transcripts on the Linux workstation agents ran grep, rg or find 2,549 times
   and `search_code` 4 times; after three of those four they went back to grep.
2. The `embeddings` table held 578 MB of a 610 MB database, and embedding was the
   only reason AXON needed a provider key and a pgvector image.
3. The memory tools are what agents call: `get_session_memory` 131,
   `axon_get_context` 120, `axon_search_lessons` 79, `axon_capture` 69.
4. Full-text lesson search on the 41 real lessons, against pgvector on the same
   queries: 20 English task queries put the expected lesson in the top 5 for 20
   of 20 (pgvector 20 of 20), first for 15 (pgvector 19); 41 trigger queries
   first for 41 of 41 (pgvector 33); about 5 ms per search against 600 ms. Five
   Portuguese queries found 3 of 5 in the top 5 (pgvector 5 of 5): lesson
   queries should be written in English.

## Consequences

- The only LLM call left is the vault hook (`vault/til_promoter`), so `litellm`
  stays as a dependency. `fastembed`, `tree-sitter`, `watchdog`, `ollama`,
  `pgvector`, `glyph-kg`, `pymupdf` and `defusedxml` are gone.
- No MCP tool is destructive any more; the dec-109 gate still applies to any
  that comes back.
- The code stays in git history if evidence ever asks for it back.
