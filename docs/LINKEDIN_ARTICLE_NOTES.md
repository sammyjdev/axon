# Prometheus — LinkedIn Article Notes (working draft)

> Purpose: capture all user-provided answers + observed metrics so far, to unblock writing the LinkedIn technical article (EN).
> This file is intentionally concise and “source of truth” for the article draft.

## Author context (for positioning)

- Senior Software Engineer (6+ years) focused on Java/Spring Boot/AWS.
- Repositioning towards AI Engineering.
- Prometheus is the anchor project for that transition; the article must signal real engineering depth, not AI hype.

## (a) Personal motivation — answers captured

### The concrete trigger / pain

- Increasing token cost and frontier model usage caps repeatedly blocked work.
- Multiple times per day, the author got “stuck” due to token limitations and not wanting to spend more money.
- Frequent need to restate the same instructions once a conversation/pipeline hit a limit.
- This happened more often as the author started relying on lower-effort / cheaper models.
- Impact: workflow stalled for hours until a reset.

### Why self-hosted / local-first

- Keep as much context as possible “with me” to continue without depending on external tools.
- Long-term direction: migrate to local agents instead of cloud agents to increase independence.

### “Second brain self-hosted” in practice (author framing)

- A single place where progress across personal projects, study, and work stays available.
- Reduce token waste: avoid sending useless context and unnecessary spend.
- The context remains available to refine/iterate over time.

## (b) Evolution — answers captured

- Built quickly; early usable iteration was close to today’s profile.
- ~1 week prior to this note: added more compression metrics, RTK integration, and local semantic compression “caveman style”.

### Major milestones (author-highlighted)

- RTK + Caveman compression pipeline.
- Mem0 memory.
- Redis dependency graph.

### Discarded / changed direction

- Initial plan: Prometheus would “use the coding tools” and drive the full process end-to-end.
- Realization: this would become too complex for daily workflow.
- Current direction: tools/agents should consume Prometheus via MCP (Prometheus as context layer), not the other way around.

### Current state

- Still optimizing and iterating on usage/workflow; not “final”.

## (c) Failures & learnings — answers captured

- No “hardest bug” story yet (nothing particularly complicated encountered so far).
- Key learning: compressed-context systems require discipline about *what to carry forward*.
  - The important constraint is selecting only the most important details so context doesn’t bloat and degrade performance.

## (d) Real metrics — observed so far

### Qdrant collection stats (Desktop, Qdrant local)

- Collection: `knowledge`
- Observed: `points_count = 21` (interpretable as ~21 chunks indexed in that collection).
- Vector size: `384`, distance: `Cosine`.

> Source: `curl -s "http://localhost:6333/collections/knowledge" | python -m json.tool`

### Metrics still missing (explicitly not measured yet)

- `pb ask` latency (p50/p95 or rough “usually/worst”).
- Total indexed files/chunks across contexts (beyond `knowledge=21` points).
- Average token reduction from Caveman + RTK (`pb cost compression`).
- Circuit breaker “opened in the wild” evidence.
- Daily/Opus budgets configured (env values) and frequency of hitting limits pre-Prometheus.

## Next planned work (for the article)

- Collect the missing metrics on Mac (where `pb` runs) and on Desktop (where Qdrant runs) via a small script.
- Continue Q&A categories in order: (e) day-to-day usage → (f) Prometheus vs market → (g) next steps.

