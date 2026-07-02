# Design — bge-m3 embedder with local→cloud provider chain (issue #45)

Date: 2026-07-02
Status: Draft (brainstorming output, pre-slice)
Issue: sammyjdev/axon#45

## Problem

Code-intent natural-language queries to `ask()` / `search_code` retrieve Markdown
doc chunks instead of the code they're about, even in code-majority contexts.
Confirmed root cause (diagnose loop): code chunks embed **raw source** (`pipeline.py`
embeds `c.content`) through `BAAI/bge-small-en-v1.5` (an English text model). Raw
code embeds mediocre (~0.5–0.7 cosine) for NL queries and loses the top-k race to
prose doc chunks. Tree-sitter is used only for chunking boundaries, not the embedded
representation.

## Decision: swap the embedding model to bge-m3 (approach B)

Empirical test (2026-07-02): with **`bge-m3`** (multilingual, dim 1024), the correct
code chunk beats the winning doc on **raw content in 5/5** prose golden cases —
without any summarization. bge-small-en scored 0/5; nomic-embed-text 1/5. bge-m3 is
already served on the desktop Ollama and is the model the sibling rpg-master project
uses.

Rejected alternatives:
- **A (summary-augmented embedding):** works (7/9) but needs an LLM summarizer
  pipeline, per-chunk LLM cost, and a summary cache. bge-m3 is simpler and fixes it
  at the model layer. Dropped.
- **C (both):** unnecessary — B alone resolves it.

## Decision: local→cloud provider chain, all serving bge-m3

Requirement (user): the embedder must keep working when the local Ollama is
unavailable. Constraint: query and chunk vectors must be numerically comparable, so
every provider in the chain must serve **the exact `BAAI/bge-m3` model** (not an
"equivalent"). Verified interchangeable — Ollama bge-m3 and NIM bge-m3 produce
**identical** vectors (cosine = 1.0000 on sample texts).

Ordered, **configurable** chain (adding a provider is config, not code):

| Order | Provider | Endpoint | Model id | Cost | Auth |
|-------|----------|----------|----------|------|------|
| 1 | Ollama (local) | `AXON_OLLAMA_LOCAL_HOST` `/api/embed` | `bge-m3` | free | none |
| 2 | NVIDIA NIM | `integrate.api.nvidia.com/v1/embeddings` | `baai/bge-m3` | free tier | `NVIDIA_NIM_API_KEY` |
| 3 | DeepInfra | `/v1/openai/embeddings` (OpenAI-compatible) | `BAAI/bge-m3` | $0.01 / 1M input | `DEEPINFRA_API_KEY` |

The embedder tries providers in order; on failure (unreachable / rate-limited /
error) it falls through to the next. All down → **fail loud** (never silently return
a wrong-space vector). Free tiers (NIM) rate-limit hard, so a paid tier is the
reliable last resort.

**Additional verified bge-m3 pay-per-use providers** (drop into the chain via config
as extra redundancy — all serve the exact model, so vectors stay interchangeable):

| Provider | Model id | Price / 1M in | API | Notes |
|----------|----------|---------------|-----|-------|
| DeepInfra | `BAAI/bge-m3` | $0.010 | OpenAI-compat `/v1/openai/embeddings` | 200 concurrent, $5 free credit, no minimum — recommended paid primary. Use the **plain `BAAI/bge-m3`** (dense 1024-dim), NOT `BAAI/bge-m3-multi` (multi-vector/sparse "multi-functionality" — different representation, not interchangeable). The cos≥0.999 onboarding check confirms the id before trusting it. |
| Novita | `baai/bge-m3` | $0.010 | custom REST | tiered RPM/TPM by recent top-up |
| OVHcloud AI Endpoints | `bge-m3` | 0.01 € | OpenAI-compat `/v1/embeddings` | limits via support |
| Cloudflare Workers AI | `bge-m3` | $0.012 | OpenAI-compat `/v1/embeddings` | 60K context |

Because most of these are OpenAI-compatible, one adapter (base_url + key + model id)
covers NIM, DeepInfra, OVH, Cloudflare; Novita needs a thin variant.

Ruled out (verified): OpenRouter (no embedding models), Groq / Cerebras (LLM-only).
Together / SiliconFlow (no confirmed `bge-m3` model id), Baseten / Replicate / HF
Inference Endpoints (self-host or per-hour / per-run billing, not clean token-metered
drop-ins).

### Provider onboarding rule

Different serving layers can vary normalization / float defaults even for the same
model id. Before a provider is trusted in the chain, it must pass an
**interchangeability check**: embed a fixed sample through the local bge-m3 and the
candidate, assert cosine ≥ 0.999. The embedder also L2-normalizes every vector it
returns, so scale differences cannot affect ranking.

## Components

- **`EmbedderEngine` (`src/axon/embedder/engine.py`)** gains a bge-m3 provider chain,
  selected by config; the `embed()` / `embed_one()` interface is unchanged so all
  callers (ingest + query) are untouched. `vector_dim()` returns 1024 for bge-m3.
  Prefer `litellm.embedding()` (already a dependency) for the OpenAI-compatible
  providers and Ollama if it supports them cleanly; otherwise thin per-provider HTTP
  adapters behind one `embed(texts) -> list[vec]` interface.
- **Table migration:** the `embeddings.vector` and `recall_embeddings.vector` columns
  move from dim 384 to **1024**. No mixed dims → a full re-index of the corpus (code
  AND docs) is required. bge-m3 is a strict upgrade for docs too (multilingual).
- **Gate:** `retrieval_eval` + the grounded 24-case golden set must show code recall
  improves before/after. The FORGE `loop.yaml` gate must widen to include
  `tests/embedder` (today it doesn't) before FORGE touches the embedder.

## Data flow (unchanged shape)

`ask()` → `_retrieve_context` → `embed_one(query)` [now via the bge-m3 chain] →
pgvector search over the re-indexed 1024-dim corpus. Ingest: `index_path` →
`_embed_in_token_batches` [now via the chain] → upsert.

## Secondary bug (separate slice)

Two golden queries (`ingest_file`, `resolve_latest_tag`) returned an **empty**
retrieval despite the symbol existing at cosine ~0.51 — a query-side filter dropping
results. Independent of the model swap; own slice.

## Non-goals

- No summarization pipeline (approach A dropped).
- No code-specific embedding model now (NIM's `nvidia/nv-embedcode-7b-v1` is a future
  upgrade, but it changes the vector space → its own re-index; out of scope).
- Not touching the tree-sitter chunker (D5 release gate) — only the embedded
  representation.

## Relations

- Fixes: issue #45.
- Relates to: dec-122 / D3 (existing Ollama + litellm integration reused for the chain).
- Requires: `_retrieve_context`, `EmbedderEngine`, `pgvector` store, `retrieval_eval`
  + golden set (from PR #44).

## FORGE slices (see docs/agent-backlog.md)

S1 widen gate · S2 bge-m3 provider chain · S3 dim migration + default swap · S4
empty-retrieval bug · S5 operational re-index. FORGE runs S1–S4; S5 is operational
(live pgvector + providers).
