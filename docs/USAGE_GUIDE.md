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

## No provider key

No AXON command or MCP tool calls an LLM or an embedding provider
([dec-138](decisions/dec-138-stop-code-vectors.md)). The only LLM call left is
the vault hook that promotes TILs (`vault/til_promoter`).

## Core Commands

AXON no longer indexes or searches code: agents read code with their own tools.
What AXON answers is what happened in earlier sessions:

```bash
axon status                 # decisions captured for this repo
axon activity search "..."  # past conversations: session, date, role, snippet
axon activity backfill      # import every Claude Code / Codex transcript (--dry-run counts)
axon note "..."             # a free-form session note
axon gain                   # historical compression telemetry
```

Inside an agent, the MCP tools do the same: `axon_get_context`,
`get_session_memory`, `axon_search`, `axon_search_lessons`.

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
has `sync` — see `axon adr --help`.

A commit is captured only when it carries a signal, with no LLM: an `arch:` or
`decision:` subject, or an `ADR-Decision: <title>` trailer, becomes a draft
decision; a `Lesson: <title>` trailer becomes a lesson draft under
`.axon/lesson-draft/`. A plain commit touches nothing.

## Career context

`career` is a context value, not a command group. `pb career metrics/brief/interview` were removed in dec-125
with no replacement.

## Expansion and Deep Research

`pb deep suggest` and `pb expand run/review/approve` were removed in
dec-125 with no replacement; there is no staged draft-review pipeline today.

## Cost and Compression

`pb cost today/week/compression` were removed in dec-125.
`axon gain [--json]` shows the compression telemetry recorded until the
compressor left with `ask` (dec-138): windows, tokens saved, daily trend and
the p50/mean/p95/max compression ratio. `docs/METRICS.md` explains each number.

```bash
axon gain
```

## Context Safety

Available contexts:

- `knowledge`
- `career`
- `personal`
- `work`

Rules worth keeping:

- Use explicit `--ctx work` only when you really want restricted retrieval.
- Do not mix vault data into the repository itself.

## Tool risk gating

MCP tools exposed by `axon serve` are classified into three risk classes
and gated by `PolicyRegistry.decide_tool_action`:

| Risk | Tools | Gate |
| --- | --- | --- |
| `read` | `axon_get_context`, `axon_search`, `axon_search_lessons`, `get_session_memory`, `get_adrs`, `axon_validation_stats`, `axon_health` | Always allowed; emits `invoke` + `output` trace stages. |
| `write` | `axon_capture`, `axon_capture_event`, `axon_session_start`, `axon_session_end`, `axon_handoff`, `axon_record_lesson`, `axon_record_outcome`, `save_adr` | Denied with `DENY_RESTRICTED_TOOL_WRITE` when `ctx` is `work`. Otherwise allowed. |
| `destructive` | none since dec-138 | Would require `AXON_ALLOW_DESTRUCTIVE` truthy (`1` / `true` / `yes` / `on`, case-insensitive), denied with `DENY_DESTRUCTIVE_NO_CONSENT` otherwise. |

Every denial emits a `ComplianceEvent` to the canonical audit log
(`axon.observability.compliance`) and a `policy` trace stage under the
call's `trace_id`.

See [`dec-109`](decisions/dec-109-tool-tracing-and-risk-gating.md) for the
full design.

## Verification metric

`axon_validation_stats` returns the verification pass rate over judged
decisions. The LLM judge that scored drafts on push is retired, so the stats
cover the decisions it scored before then.
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
# start of day — the SessionStart hook prints .axon/context.md; or ask the agent
# to call axon_get_context / get_session_memory
axon status

# while working
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

Enable it by passing a similarity seam. AXON ships no embedder since dec-138,
so bring any object with `embed_one(text) -> list[float]`:

```python
from axon.recall.strategy import recall_context
from axon.recall.supersession import make_embedding_similarity

out = await recall_context(
    repo,
    store=store,
    enable_supersession=True,
    similarity=make_embedding_similarity(my_embedder),
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
axon doctor
```

If those fail, the problem is usually one of:

- env vars not loaded in the current shell (`AXON_PG_URL` in particular —
  defaults to port 5433, but a `docker-compose.override.yml` can remap it)
- the `axon-postgres` container not running (`docker compose ps axon-postgres`)
- vault path mismatch
