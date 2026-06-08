# Usage Guide

This guide covers the day-to-day AXON workflow after the engine and vault
are already set up.

## Session Checklist

Before starting a work session:

```bash
cd /path/to/axon
set -a
source .env.local
set +a

pb --help
docker compose ps
```

If your repository lives elsewhere, export `AXON_ENGINE` first.

## Provider Profile

`pb ask` and any other command that hits an LLM uses the active provider
profile (`AXON_PROVIDER_PROFILE`, default `free`).

| Profile | Required env | What it routes to |
| --- | --- | --- |
| `free` | `GROQ_API_KEY`, `NVIDIA_NIM_API_KEY` | Groq + NVIDIA NIM free tiers |
| `paid` | `OPENROUTER_API_KEY`, `GROQ_API_KEY` | OpenRouter Claude (D2 tiers) + Groq paid |

The rate-limit gate is on by default for free-tier providers — if your daily
workflow includes heavy ingest, monitor for `DENY_RATE_LIMIT` errors and tune
`AXON_GROQ_MAX_RPM` / `AXON_GROQ_MAX_RPD` (or move to the `paid` profile).
Full reference: `docs/decisions/dec-106-routing-profiles.md`.

## Core Commands

### Ask for context

`pb ask` retrieves relevant chunks, compresses them, and prints prompt-ready
output.

```bash
pb ask "How should I model semantic indexing for mixed code and markdown?"
pb ask "Summarize the current context for the auth module" --rtk-max-tokens 600
```

What it does:

1. Detects or validates the context.
2. Searches the allowed collections.
3. Compresses the retrieved material.
4. Prints planner and executor prompts.

### Search directly

Use `pb search` when you want raw hits instead of the full prompt pipeline.

```bash
pb search "uuid5 qdrant" --ctx knowledge
pb search "service layer" --ctx personal --lang python --top 10
```

### Index a path

```bash
pb index ~/vault/knowledge --ctx knowledge
pb index ~/vault/personal --ctx personal
```

This writes semantic chunks to Qdrant and code dependency relationships to
Redis.

### Index development repositories from a manifest

```bash
pb index-dev --dry-run
pb index-dev --project axon
```

Use `--dry-run` first when validating a manifest-driven setup.

### Watch for changes

```bash
pb watch ~/vault/knowledge --ctx knowledge
```

Use the watcher when you want near-real-time reindexing. For small or
infrequently updated vaults, manual indexing is often enough.

## Knowledge Capture

### Save a TIL

```bash
pb til "Qdrant ids should use uuid5 instead of raw SHA1 hex" --tags qdrant,ids
```

### List pending TILs

```bash
pb til --list
```

### Promote today's TILs

```bash
pb til --promote-today
```

### Convert one TIL into a HOW-TO

```bash
pb til howto --from knowledge/daily/2026-05-05/til-example.md
```

## ADR Workflow

```bash
pb adr add --project axon --title "Use UUID5 for deterministic Qdrant ids"
pb adr list --project axon
```

Use ADRs for decisions that should remain queryable later.

## Career and Memory Commands

```bash
pb career metrics
pb career brief "Target Company"
pb career interview "kafka"

pb memory smoke --ctx knowledge
```

`pb memory smoke` is the fastest way to validate the Mem0 (Qdrant) integration
path.

## Expansion and Deep Research

```bash
pb deep suggest

pb expand run --ctx knowledge --topic "vector search" --fast
pb expand review ~/vault/knowledge/staging/vector-search.md
pb expand approve ~/vault/knowledge/staging/vector-search.md
```

The expansion flow is intentionally staged. Review happens before publication
to the final vault path.

## Cost and Compression

```bash
pb cost today
pb cost week
pb cost compression
```

These commands help track whether compression and provider routing are working
as expected over time.

## RTK Helpers

AXON includes helper commands around the external RTK binary when it is
installed:

```bash
pb rtk-status
pb rtk-init --agent codex
pb rtk-proxy "git status"
pb rtk "git diff"
```

If RTK is not installed, the main AXON workflows still work.

## Context Safety

Available contexts:

- `knowledge`
- `career`
- `personal`
- `work`

Rules worth keeping:

- Use explicit `--ctx work` only when you really want restricted retrieval.
- Do not mix vault data into the repository itself.
- Reindex after structural moves if you are not running the watcher.

## Tool risk gating

MCP tools exposed by `axon serve` are classified into three risk classes
and gated by `PolicyRegistry.decide_tool_action`:

| Risk | Tools | Gate |
| --- | --- | --- |
| `read` (11 tools) | `axon_get_context`, `axon_search`, `axon_handoff`, `axon_validation_stats`, `axon_health`, `search_code`, `ask`, … | Always allowed; emits `invoke` + `output` trace stages. |
| `write` (5 tools) | `axon_capture`, `axon_session_start`, `axon_session_end`, `axon_capture_event`, `save_adr` | Denied with `DENY_RESTRICTED_TOOL_WRITE` when `ctx` is `work`. Otherwise allowed. |
| `destructive` (2 tools) | `axon_export_now`, `axon_mark_done` | Require `AXON_ALLOW_DESTRUCTIVE` truthy (`1` / `true` / `yes` / `on`, case-insensitive). Denied with `DENY_DESTRUCTIVE_NO_CONSENT` otherwise. RESTRICTED ctx is denied as for writes. |

Every denial emits a `ComplianceEvent` to the canonical audit log
(`axon.observability.compliance`) and a `policy` trace stage under the
call's `trace_id`.

```bash
# enable destructive tools for the current shell
export AXON_ALLOW_DESTRUCTIVE=1
```

See [`dec-109`](decisions/dec-109-tool-tracing-and-risk-gating.md) for the
full design.

## Verification metric

`axon_validation_stats` returns the verification pass rate over judged
decisions (LLM judge in `_judge_and_export` scores each draft on push).
The aggregate accepts a `repo` filter (`repo=None` aggregates the whole
workspace) and a `threshold` (must be `> 0`; the default `3.5` matches
the judge's 0–5 scale).

```python
axon_validation_stats(repo="axon", threshold=3.5)
# → {"n_total": 12, "n_scored": 9, "n_passed": 6, "pass_rate": 0.6667,
#    "threshold": 3.5}
```

Internally, `Decision.judged: bool` is the source of truth for
"already scored"; `validation_score == 0.0` is **no longer** treated as
the unjudged sentinel, so legitimately-bad decisions are not re-judged
on every push.

## Recommended Daily Loop

```bash
# start of day
pb ask "What should I know before resuming this project?"

# while working
pb search "previous decision about indexing" --ctx personal
pb til "important implementation note" --tags project-x

# end of day
pb til --list
pb til --promote-today
```

## MCP Usage

AXON also exposes the same knowledge through MCP for agentic tools such
as Claude Code and Copilot. The local CLI remains the easiest way to validate
behavior before relying on editor integrations.

## Recall supersession (opt-in)

`recall_context` can demote *superseded* decisions so a stale one never outranks
the decision that revised it. It is **off by default** — the legacy ranking is
byte-for-byte unchanged unless you opt in.

Enable it by passing a similarity seam (offline, via the local embedder):

```python
from axon.recall.strategy import recall_context
from axon.recall.supersession import make_embedding_similarity
from axon.embedder.engine import EmbedderEngine

out = await recall_context(
    repo,
    store=store,
    enable_supersession=True,
    similarity=make_embedding_similarity(EmbedderEngine()),
)
```

A stale decision is **demoted, never dropped** (rank × 0.02) — it stays fully
recallable. Detection requires shared scope (overlapping files/symbols) **and** a
confirmed revision: a revision verb in the newer summary (EN/PT, e.g.
`drop`/`replace`/`substitui`) or a near-duplicate (cosine ≥ 0.93). Additive work
in the same area is deliberately *not* treated as supersession.

Default-off on purpose: real-data validation suppressed false positives but has
not yet measured recall on reworded revisions. See
[`docs/decisions/dec-115-supersession-ranking-penalty.md`](decisions/dec-115-supersession-ranking-penalty.md).

## When Something Looks Wrong

Check these first:

```bash
docker compose ps
pb search "health check" --ctx knowledge --top 1
pb memory smoke --ctx knowledge
```

If those fail, the problem is usually one of:

- env vars not loaded in the current shell
- local services not running
- vault path mismatch
- no indexed content for the queried context
