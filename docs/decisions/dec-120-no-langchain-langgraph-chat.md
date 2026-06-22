# dec-120 — No LangChain / LangGraph / built-in chat; keep the governed router and lean deps

- Status: accepted
- Date: 2026-06-20
- Relates to: D5 (chunker quality is a release gate), dec-116/117/118
  (narrow-and-deep, swappable stack with a lean dependency surface), dec-119
  (read-only dashboard as the sanctioned UI). Does not change dec-119.

## Context

While planning user-facing surfaces, three additions were considered: adopting
**LangChain**, adopting **LangGraph**, and building a **chat** interface into
AXON. Each was evaluated against what AXON already has rather than in the
abstract.

### LangChain

AXON has already standardised on **`litellm`** as its provider-abstraction layer
(`router/engine.py`, `router/classifier.py`, `router/compressor.py`,
`validation/judge.py`, `adr/inference.py`). LangChain's value props land exactly
on areas where AXON owns a *differentiated* implementation:

| LangChain offers | AXON already has | Net |
| --- | --- | --- |
| Provider/model abstraction | `litellm` + governed router (profiles, budget, rate-limit gates) | none; would lose the governance |
| Text splitters / loaders | tree-sitter chunker — a **release gate (D5)** | negative; trades a differentiator for generic |
| Retrievers / RAG chains | GLYPH + `RetrievalStrategy`/`ContextPack` | none |
| Structured output / parsers | `litellm` + Pydantic v2 | none |

The only non-overlapping upside is LangChain's large catalogue of pre-built SaaS
loaders — out of AXON's current scope (capture is git/session-driven, not
multi-source SaaS ingest).

### LangGraph

LangGraph is stateful multi-step agent orchestration. AXON's flows are not that
shape: capture is deterministic and event-driven (dec-104), recall is retrieval,
ADR inference is a single-pass judge. AXON also already owns its orchestration —
the governed `router/engine.py` (task-tier routing, budget/rate-limit gates) and
the staged `expansion/service.py` with budget enforcement. LangGraph would
overlap purpose-built, governed code and pull in ecosystem weight. (Its memory
arm, LangMem, is itself a competitor in the space AXON occupies.)

### Built-in chat

A conversational surface inside AXON would reinvent the MCP client: the "chat"
already exists as Claude Code / Codex / Cursor consuming AXON over MCP. AXON also
already ships a single-shot query path (`axon ask`). A consumer-facing chat is
scope drift for what is an *engine*, not an assistant.

## Decision

1. **Do not adopt LangChain.** It offers nothing AXON lacks and would replace
   differentiated assets (the D5 chunker, the governed `litellm` router) with
   generic equivalents at the cost of ecosystem weight.

2. **Do not adopt LangGraph.** AXON's flows are deterministic, not agentic
   loops, and its own router/expansion layers already provide governed
   orchestration. Re-platforming onto LangGraph would be a rewrite for negative
   value.

3. **Do not build a chat interface.** The MCP clients are the chat, and
   `axon ask` covers single-shot queries. At most, a read-only **search box**
   may live inside the dashboard (dec-119) as a demo of `recall_context` — that
   is search UI, not a conversational agent.

4. **The sanctioned UI is the read-only dashboard of dec-119.** "Yes to a UI"
   means rendering the canonical stores, not adding an agent framework or a chat
   runtime.

This is the dec-115/117/118 pattern once more: keep the architecture and the
dependency surface lean; borrow concepts, not frameworks.

## Consequences

- No new runtime framework dependency. `litellm` remains the provider seam; the
  tree-sitter chunker and the governed router remain AXON's own.
- The portfolio signal is "AXON has a purpose-built governed router," which is
  stronger than "AXON wires LangGraph."
- If multi-source SaaS ingestion ever enters scope, LangChain's loader catalogue
  can be re-evaluated as an **optional, eval-/ingest-only** extra — never on the
  core path.

## Open follow-ups

- None. Revisit only if a genuinely multi-step agentic workflow or multi-source
  ingestion requirement emerges that the current router/expansion layers cannot
  express.
