# Usage Guide

This guide covers the day-to-day Prometheus workflow after the engine and vault
are already set up.

## Session Checklist

Before starting a work session:

```bash
cd /path/to/prometheus
set -a
source .env.local
set +a

pb --help
docker compose ps
```

If your repository lives elsewhere, export `AXON_ENGINE` first.

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
pb index-dev --project prometheus
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
pb adr add --project prometheus --title "Use UUID5 for deterministic Qdrant ids"
pb adr list --project prometheus
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

Prometheus includes helper commands around the external RTK binary when it is
installed:

```bash
pb rtk-status
pb rtk-init --agent codex
pb rtk-proxy "git status"
pb rtk "git diff"
```

If RTK is not installed, the main Prometheus workflows still work.

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

Prometheus also exposes the same knowledge through MCP for agentic tools such
as Claude Code and Copilot. The local CLI remains the easiest way to validate
behavior before relying on editor integrations.

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
