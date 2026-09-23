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

axon --help
docker compose ps
```

If your repository lives elsewhere, export `AXON_ENGINE` first.

## Provider Profile

The `ask` MCP tool and any other command that hits an LLM uses the active
provider profile (`AXON_PROVIDER_PROFILE`, default `budget`).

| Profile | Required env | What it routes to |
| --- | --- | --- |
| `budget` | `GROQ_API_KEY`, `NVIDIA_NIM_API_KEY` | Groq + NVIDIA NIM free tiers |
| `paid` | `OPENROUTER_API_KEY`, `GROQ_API_KEY` | OpenRouter Claude (D2 tiers) + Groq paid |

The rate-limit gate is on by default for free-tier providers — if your daily
workflow includes heavy ingest, monitor for `DENY_RATE_LIMIT` errors and tune
`AXON_GROQ_MAX_RPM` / `AXON_GROQ_MAX_RPD` (or move to the `paid` profile).
Full reference: `docs/decisions/dec-106-routing-profiles.md`.

## Core Commands

### Ask for context

There is no `axon ask` CLI command. Retrieval-and-compress lives only as the
MCP `ask` tool (`src/axon/mcp/server.py`), called by an agent (Claude Code,
Codex, …) through `axon serve`, not typed at a terminal. Use `axon search`
below for a terminal-driven equivalent.

### Search directly

Use `axon search` when you want raw hits instead of the full prompt pipeline.

```bash
axon search "service layer" --ctx personal --lang python --top 10
axon search "uuid5 postgres ids" --ctx knowledge
```

### Index the vault

```bash
axon index-vault --dry-run
axon index-vault
```

This writes semantic chunks and code-dependency relationships (the `dep:*`
graph) into the shared Postgres store — `pgvector` for vectors, the
`symbol_deps` table for the graph (dec-121; Qdrant and Redis were retired).

### Index development repositories from a manifest

```bash
axon index-dev --dry-run
axon index-dev --project axon
```

Use `--dry-run` first when validating a manifest-driven setup.

### Watch for changes

There is no watcher. `pb watch` was removed in dec-125; reindex manually
(`axon index-vault` / `axon index-dev`) after a batch of changes.

## Knowledge Capture

The TIL pipeline (`pb til`: save / list / promote / convert-to-howto) was
removed in dec-125 along with the rest of `pb`. The closest thing today is a
free-form session note:

```bash
axon note "Qdrant ids should have used uuid5 instead of raw SHA1 hex"
```

`axon note` is an alias for `axon session note` — one free-text note per
call, no list/promote/howto pipeline.

## ADR Workflow

```bash
axon adr add --project axon --title "Use UUID5 for deterministic decision ids"
axon adr list --project axon
```

Use ADRs for decisions that should remain queryable later. `axon adr` also
has `sync`, `infer-commit`, `review`, `audit` and `validate-drafts` — see
`axon adr --help`.

## Career context

`career` is a `--ctx` value (`axon search "..." --ctx career`), not a
command group. `pb career metrics/brief/interview` were removed in dec-125
with no replacement.

## Expansion and Deep Research

`pb deep suggest` and `pb expand run/review/approve` were removed in
dec-125 with no replacement; there is no staged draft-review pipeline today.

## Cost and Compression

`pb cost today/week/compression` were removed in dec-125.
`axon gain [--json]` is the current source: windows, tokens saved, daily
trend and the p50/mean/p95/max compression ratio. `docs/METRICS.md` is the
maintained reference for what each number means.

```bash
axon gain
```

## RTK Helpers

AXON includes helper commands around the external RTK binary when it is
installed:

```bash
axon rtk-status
axon rtk-init --agent codex
axon rtk-proxy "git status"
axon rtk "git diff"
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
- Reindex (`axon index-vault` / `axon index-dev`) after structural moves —
  there is no watcher to pick them up automatically.

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
# start of day — ask the `ask` MCP tool through your agent, or search directly
axon search "what should I know before resuming this project" --ctx personal

# while working
axon search "previous decision about indexing" --ctx personal
axon note "important implementation note for project-x"

# end of day
axon status
axon gain
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
axon health
axon search "health check" --ctx knowledge --top 1
```

If those fail, the problem is usually one of:

- env vars not loaded in the current shell (`AXON_PG_URL` in particular —
  defaults to port 5433, but a `docker-compose.override.yml` can remap it)
- the `axon-postgres` container not running (`docker compose ps axon-postgres`)
- vault path mismatch
- no indexed content for the queried context
