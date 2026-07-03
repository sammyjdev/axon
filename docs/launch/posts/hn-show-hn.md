> **STALE DRAFT (2026-07-04): do not publish as-is.** This draft cites the
> retired 52.3% projection (a deterministic model, never a measurement). The
> measured claims - session cost parity with crossover at turn 6-9, recall
> faithfulness uplift 0.40-0.52 -> 0.72-0.76, 90.7% real-usage savings vs
> full-file reads - live in `docs/METRICS.md` and gnomon-eval ADR-0011.
> Rewrite against those numbers before publishing.

# platform: Hacker News
# post_type: Show HN
# title: Show HN: AXON – Same context across Claude Code, Codex, and Cursor
# notes: Submit at https://news.ycombinator.com/submit — paste the title line
#        as-is, then the body below. No markdown is rendered on HN; the body
#        is plain text. Strip the YAML header before posting.

---

Show HN: AXON – Same context across Claude Code, Codex, and Cursor

Every time you switch AI coding agents — or resume a project after a few days
away — the assistant starts cold. You re-explain the same architectural
decisions, re-describe what you were in the middle of, and watch the model
confidently ignore constraints it was told about three sessions ago.

AXON captures context at the moments it crystallises: git commit, post-push,
and session start/end hooks. It stores everything in SQLite (source of truth),
fans out to Redis (graph cache) and Qdrant + mem0 (vector + semantic memory),
and surfaces context on demand either over MCP or through a plain
`.axon/context.md` file in the repo.

**Architecture in one paragraph:**

Git hooks fire on commit/push/init. Session hooks fire when your agent starts
and ends. Both paths write to SQLite. An LLM judge running on those events
infers architectural decisions and open questions. The MCP server exposes tools
(`axon_get_context`, `axon_search`, `axon_handoff`) so any MCP-capable agent
can pull the right context slice without being handed the whole transcript.
Agents that do not speak MCP (or are running without the server configured)
fall back to reading `.axon/context.md` — a file AXON writes and keeps in sync
automatically.

**Capture is event-driven, not timer-driven.** There is no background process
polling for changes. Cost is zero when nothing is happening.

**Token savings (be skeptical — read the methodology):**

A deterministic cost model of a 20-turn coding session shows 52.3% fewer input
tokens vs. a baseline that re-supplies full project context every turn
(87,000 tokens baseline, 41,500 with AXON). This is a modelled benchmark, not
an instrumented live measurement. The benchmark script and all assumptions are
in `benchmarks/README.md`. I included honest caveats about where the model
deviates from real sessions; run it yourself and see if the parameters match
your workflow.

**Status:**

Alpha. Phases 0–7 of the refactor are done. Not yet on PyPI — install from
source. There are gaps I know about (the cross-agent MCP handoff path needs
more hardening, test coverage on the session hook serialiser is thin). I'm
posting here to get feedback on the design before cutting a proper release.

**What I want feedback on:**

1. Is the `.axon/context.md` file fallback the right interface for agents that
   do not support MCP, or is there a better convention?
2. SQLite as the source of truth felt right for a single-developer install.
   At what scale does that break down?
3. The benchmark model assumes baseline re-sends accumulated decision context
   (+300 tokens/turn). Is that conservative or generous relative to how your
   agents actually behave?

Repo: https://github.com/sammyjdev/axon
License: MIT, self-hosted only — no telemetry, no cloud dependency.
