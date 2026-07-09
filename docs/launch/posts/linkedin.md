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

**One store, one source of truth**

The storage stack used to be four components (SQLite, Redis, Qdrant, mem0),
each with a distinct job. I have since consolidated all of it onto a single
PostgreSQL instance with pgvector for embeddings — sessions, decisions, the
code-dependency graph, and code vectors all live in the same database now.

That was not the original design. It started split by role — a cache here, a
vector store there — and the operational cost of running and reasoning about
four stores (which one is authoritative, what is a projection, what happens
when they drift) outweighed the marginal win of treating graph cache and
vector search as separate systems. Postgres with pgvector, plus a well-indexed
dependency table for the code graph, turned out fast enough, and there is
exactly one place to look when something is wrong.

Neo4j was evaluated and dropped earlier in the project. The graph use cases
here (code dependency, decision chains) do not need a full property graph
engine, and the operational complexity is not justified for a
single-developer install. That decision is documented in the repo.

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
