# platform: LinkedIn
# post_type: Text post (no article)
# notes: LinkedIn renders line breaks but not markdown headers or code blocks.
#        The post is formatted to read well as plain paragraphs.
#        Strip this header before posting.

---

I have been working on a side project that solves a specific problem in AI
coding workflows: context evaporates when you switch agents or resume a project
after time away.

The project is called AXON — agent-agnostic execution and context network. It
is alpha, Apache-2.0-licensed, and self-hosted. Here is the design I landed on after
a few iterations.

**Event-driven capture, not time-driven**

The first architectural decision that mattered: do not poll. AXON captures
context only at moments when context actually changes — git commit, git push,
git init, and agent session start/end. An LLM judge runs on each event and
extracts architectural decisions and open questions into structured storage.
Between events, the system is completely idle. This was a deliberate choice to
avoid the "always-on context daemon" pattern, which has real cost at scale and
non-trivial privacy surface.

**Triple-storage with clear role separation**

The storage stack has four components, each with a distinct job:

SQLite is the source of truth. Every capture event writes a durable record
here first. Nothing else is authoritative — Redis and Qdrant are projections
of SQLite state, not independent stores.

Redis holds the graph cache: dependency relationships between code symbols and
modules. Graph traversal over Redis is fast enough for real-time MCP tool
calls; computing it from SQLite on every query would not be.

Qdrant provides code vector search and backs the mem0 layer for semantic
similarity queries. When an agent calls `axon_search`, the query hits Qdrant
first, then Redis for graph context, then SQLite for the full record.

mem0 provides the semantic memory layer on top of Qdrant — it handles the
retrieval logic so AXON does not need to manage embedding similarity manually.

Neo4j was evaluated and dropped. The graph use cases here (code dependency,
decision chains) do not need a full property graph engine, and the operational
complexity is not justified for a single-developer install. That decision is
documented in the repo.

**Recall over MCP, with a plain-file fallback**

The primary agent interface is MCP (stdio). The tools `axon_get_context`,
`axon_search`, and `axon_handoff` give agents a query interface so they pull
only the context slice relevant to the current task, rather than being handed
a full transcript.

For agents that do not support MCP, or setups where the MCP server is not
configured, AXON writes a `.axon/context.md` file to the repo root and updates
it on every capture event. Any agent can read it. A teammate can read it.

**Token savings (model, not measurement)**

A deterministic cost model of a 20-turn coding session shows 52.3% fewer input
tokens compared to a baseline that re-supplies the full project context on every
turn (87,000 tokens baseline vs. 41,500 with AXON). That is a modelled
benchmark — it does not run real inference, and the parameters are documented
so you can evaluate whether they match your workflow. The methodology and
caveats are in the benchmarks README.

**Status and feedback**

Alpha. Phases 0 through 7 of the refactor are complete. Not yet on PyPI —
install from source. I am specifically looking for feedback on the storage
architecture at higher scale and on whether the event-driven capture model
holds up in larger team workflows where multiple developers are committing
concurrently.

Repo: https://github.com/sammyjdev/axon
