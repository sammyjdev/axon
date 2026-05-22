# AXON

**Same context, any AI coding agent.**

Every time you switch coding agents — from Claude Code to Codex to Cursor — or
resume a project after a few days away, your AI assistant starts blank. AXON
solves this by capturing context at the moments it crystallises (git commits,
session boundaries) and surfacing it on demand over MCP or a plain
`.axon/context.md` file that any agent can read. One install, continuous memory,
any agent.

---

## Quickstart

AXON is not yet on PyPI. Install from source:

```bash
git clone https://github.com/samjrdev/axon.git
cd axon
pip install -e .
```

Initialize AXON in a repo (installs git hooks and indexes the code):

```bash
axon init /path/to/your-repo
```

Register the MCP server with your coding agent. For Claude Code, add this to
your project's `.claude/settings.json`:

```json
{
  "mcpServers": {
    "axon": {
      "command": "axon",
      "args": ["serve"]
    }
  }
}
```

`axon serve` runs the MCP server over stdio. Once registered, the tools
`axon_get_context`, `axon_capture`, `axon_handoff`, `axon_search`,
`axon_export_now`, and `axon_health` are available inside your agent session.

---

## How it works

```mermaid
flowchart LR
    subgraph Capture
        GE[git events\npost-commit / post-push / init]
        SH[session hooks\nstart / end]
    end

    subgraph Storage
        SQ[(SQLite\nsource of truth)]
        RD[(Redis\ngraph cache)]
        M0[(mem0\nsemantic memory)]
    end

    subgraph Recall
        MCP[MCP tools\naxon_get_context\naxon_search\naxon_handoff]
        CF[.axon/context.md\nfile fallback]
    end

    subgraph Agents
        CC[Claude Code]
        CX[Codex]
        CU[Cursor]
    end

    GE --> SQ
    SH --> SQ
    SQ --> RD
    SQ --> M0
    SQ --> MCP
    SQ --> CF
    MCP --> CC
    MCP --> CX
    MCP --> CU
    CF  --> CX
    CF  --> CU
```

Capture is **event-driven only** — git commit/push/init and agent session
start/end. No background timer, no idle cost (see
[dec-104](docs/decisions/dec-104-event-driven-not-time-driven.md)).

Storage is: **SQLite** (source of truth) + **Redis** (graph cache) + **mem0**
(semantic memory over Qdrant). Neo4j was evaluated and dropped
([dec-101](docs/decisions/dec-101-revoke-d4-drop-neo4j.md)).

The primary transport is **MCP (stdio)**. A `.axon/context.md` file in the repo
is kept in sync as a fallback for agents without MCP support
([dec-103](docs/decisions/dec-103-cross-agent-mcp-primary.md)).

---

## Use cases

### Agent handoff without context loss

You spend an afternoon with Claude Code, push a branch, then continue the work
in Codex the next day. Without AXON, Codex starts cold. With AXON, the MCP tool
`axon_handoff` supplies Codex with the decisions, open questions, and code
index from the previous session — no copy-pasting required.

### Multi-day project continuity

On a project that spans weeks, the important context is not your last five
messages but the architectural decisions made three sprints ago. AXON captures
decisions from commit messages and session summaries into SQLite, so `axon
search` and `axon_get_context` return what actually matters, not stale history.

### Auto-generated architecture docs

AXON's LLM judge infers architectural decisions from commits and session events.
`axon export adr` and `axon export architecture` write structured Markdown notes
to an Obsidian vault, turning captured context into living documentation without
any manual ADR writing.

---

## How AXON compares

| | AXON | Aider | Cline | mem0 (standalone) |
|---|---|---|---|---|
| **Primary goal** | Agent-agnostic context continuity | Git-native AI pair programmer | AI agent inside VS Code | General-purpose semantic memory |
| **Context capture** | git events + session hooks | Conversation history | Conversation history | Explicit add/search API |
| **Works across agents** | Yes (MCP + file fallback) | No (Aider-specific) | No (Cline-specific) | Needs custom integration |
| **Git hook integration** | First-class (`axon install-hooks`) | First-class (core feature) | No | No |
| **Self-hosted** | Yes | Yes | Depends on VS Code | Yes (open-source) |
| **Storage** | SQLite + Redis + mem0 | Flat files + git | Flat files | Qdrant / Postgres |

AXON's distinctive angle is agent-agnostic context continuity — it is not a
replacement for Aider's editing workflow or Cline's VS Code integration. If you
only use one agent and one machine, those tools' built-in histories may be
sufficient. AXON adds value when you switch agents, hand off between
collaborators, or need decisions to survive across long project timelines.

---

## Token savings

A modelled 20-turn coding session shows that AXON's selective context recall
reduces input token consumption by **52.3%** compared to a baseline that
re-supplies the full project context on every turn (87,000 tokens baseline vs.
41,500 tokens with AXON). This is a deterministic cost model, not an
instrumented live measurement — see [`benchmarks/README.md`](benchmarks/README.md)
for the assumptions, caveats, and how to run it yourself.

---

## Documentation

| Document | Contents |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Architecture decisions, code conventions, agent rules |
| [`docs/ADR.md`](docs/ADR.md) | Active architectural decision records |
| [`docs/ARD.md`](docs/ARD.md) | Architectural requirements |
| [`docs/USAGE_GUIDE.md`](docs/USAGE_GUIDE.md) | CLI workflows |
| [`docs/VAULT_SETUP.md`](docs/VAULT_SETUP.md) | Obsidian vault bootstrap |
| [`docs/decisions/`](docs/decisions/) | Individual decision records (dec-100 – dec-105) |
| [`benchmarks/README.md`](benchmarks/README.md) | Token savings benchmark |

---

## Contributing

Start from tests. The repo uses TDD: bugfixes begin with a regression test,
features need testable acceptance criteria before implementation.

```bash
pytest tests/ -q
```

See [`CLAUDE.md`](CLAUDE.md) for code conventions and agent rules.

---

## License

MIT — see [`LICENSE`](LICENSE).
