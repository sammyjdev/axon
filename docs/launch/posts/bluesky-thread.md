> **STALE DRAFT (2026-07-04): do not publish as-is.** This draft cites the
> retired 52.3% projection (a deterministic model, never a measurement). The
> measured claims - session cost parity with crossover at turn 6-9, recall
> faithfulness uplift 0.40-0.52 -> 0.72-0.76, 90.7% real-usage savings vs
> full-file reads - live in `docs/METRICS.md` and gnomon-eval ADR-0011.
> Rewrite against those numbers before publishing.

# platform: Bluesky
# post_type: Thread (5–8 posts)
# notes: Each numbered post is one Bluesky post. Keep each under ~300 chars.
#        Bluesky does not render markdown — plain text only.
#        Post sequentially as replies to build the thread.
#        Strip this header before posting.

---

1/7

I built a small benchmark model for AXON, a context-continuity layer for AI
coding agents. In a modelled 20-turn session, AXON's selective recall uses
52.3% fewer input tokens than re-sending full project context every turn.

Here's what the model actually says (and doesn't say).

---

2/7

The numbers: baseline (full context re-send every turn) accumulates 87,000
input tokens over 20 turns. AXON, with a fixed 2,000-token recall budget per
turn, hits 41,500 total.

That's the model. It's deterministic math, not a live measurement.

---

3/7

How the baseline grows: turn 1 is 1,500 tokens (base context). Each subsequent
turn adds 300 tokens of accumulated decision context. By turn 20 that's 7,200
tokens per turn.

AXON stays flat at 2,000 tokens per turn after turn 1 by only recalling what
the current task needs.

---

4/7

Honest caveats: this does NOT run actual inference. Real session costs vary
with compression, context misses, and whether your agent re-sends conversation
history (which would push baseline costs higher, not lower). Use it to
understand relative trends, not as an absolute prediction.

---

5/7

The original design target was >60% savings. This model reaches 52.3% under
conservative assumptions. I didn't adjust the parameters to hit the target.
The benchmark README explains this explicitly.

Methodology and all assumptions: github.com/sammyjdev/axon/blob/master/benchmarks/README.md

---

6/7

What AXON actually is: event-driven (git hooks + session hooks) context
capture → SQLite + Redis + Qdrant + mem0 → MCP tools or a plain
.axon/context.md file. Works across Claude Code, Codex, Cursor.

Alpha, not on PyPI, install from source.

---

7/7

Posting here because I want feedback on the benchmark model specifically —
are the baseline assumptions (300 tokens/turn growth, 20 turns) representative
of how your agents behave?

Repo: https://github.com/sammyjdev/axon — Apache-2.0, fully self-hosted.
