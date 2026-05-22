# platform: Reddit — r/ClaudeAI
# suggested_title: I built a tool that hands off context from Claude Code to
#                  Codex (or any other agent) without re-explaining everything
# notes: Post as a text post. Markdown renders normally on Reddit.
#        Strip this header before posting.

---

**I built a tool that hands off context from Claude Code to Codex without
re-explaining everything**

Here is the situation I kept running into: spend a few hours with Claude Code
on a feature branch, make real progress, then want to switch to Codex (or
Cursor, or just come back the next morning). The new session starts completely
cold. I end up writing a wall of context at the top of the conversation —
"here is the architecture, here is what we decided about the auth layer, here
is what is still open." Half the time I forget something important and the
model goes off in the wrong direction.

So I built AXON to solve the handoff problem specifically.

**How it works for the Claude Code → Codex case:**

1. You run `axon init` in your repo. This installs git hooks (post-commit,
   post-push) and adds a session hook that fires when Claude Code starts and
   ends.
2. As you work, AXON captures context at those event boundaries — not
   continuously, just at commits and session edges. An LLM judge runs on each
   event and extracts architectural decisions and open questions into SQLite.
3. When you open Codex (or whatever is next), you register the AXON MCP server
   in that agent's config. The tool `axon_handoff` supplies Codex with the
   decisions, open questions, and code index from the previous session.

No copy-pasting. No wall of context in the first message.

**What if the next agent does not support MCP?**

AXON also writes a `.axon/context.md` file in the repo root and keeps it in
sync. Any agent can just read that file — you can even paste it yourself. It is
not as query-able as the MCP tools, but it is always there as a fallback.

**Honest status:**

This is alpha software. Not on PyPI yet — you install from source. I have been
using it on my own projects, but it has not had wide testing. There are known
gaps, especially around the session hook serialiser. I am posting here to get
feedback before a proper release.

The storage stack is SQLite (source of truth) + Redis (graph cache) + Qdrant
and mem0 (vector and semantic memory). There is a deterministic cost model in
the benchmarks folder that shows 52.3% fewer input tokens in a modelled 20-turn
session vs. re-sending full context every turn — but that is a model, not a
live measurement. Read the methodology before citing it.

Repo: https://github.com/axon-ai/axon (MIT)

Happy to answer questions about the design. Specifically curious whether anyone
has a cleaner approach to the session boundary detection problem — right now I
am relying on agent lifecycle hooks, which requires each agent to be explicitly
configured.
