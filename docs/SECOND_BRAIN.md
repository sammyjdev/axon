# Run AXON as Your Second Brain in Claude Code

AXON can run as a low-cost, always-available "second brain" inside Claude Code — providing semantic code search, architectural decisions, and session memory without metered API charges.

---

## Why This Works

- **FREE profile** uses Groq + NVIDIA NIM free tiers (no cost, rate-limited)
- **Local embeddings** keep all semantic search on your machine (via Qdrant)
- **Local judge** (GNOMON) scores decisions without cloud calls
- **MCP transport** runs under your Claude Code subscription (flat-rate, no per-call charge)

The result: continuous context across coding sessions, projects, and agent switches — without incremental API spend.

---

## Low-Cost Setup: Three Options

### Option 1: FREE Profile (Default, Zero Cost)

Groq + NVIDIA NIM free tiers. Rate-limited but sufficient for development work.

#### Env vars

```bash
# Use the default FREE profile (no explicit setting needed)
# or set it explicitly:
export AXON_PROVIDER_PROFILE=free

# API keys for free-tier access
export GROQ_API_KEY=gsk_your_groq_key
export NVIDIA_NIM_API_KEY=nvapi_your_nim_key
```

#### Example `.env` file

```bash
# AXON configuration
AXON_PROVIDER_PROFILE=free
AXON_ENGINE=~/dev/axon
AXON_VAULT=~/vault

# Free-tier API keys
GROQ_API_KEY=gsk_your_groq_key
NVIDIA_NIM_API_KEY=nvapi_your_nim_key

# Optional: Qdrant and Redis (defaults: localhost:6333 and localhost:6379)
# QDRANT_URL=http://localhost:6333
# REDIS_URL=redis://localhost:6379
```

#### Cost

**$0/month** (subject to free-tier rate limits: ~25 requests/min from Groq, ~50 requests/min from NVIDIA NIM).

---

### Option 2: LOCAL Ollama Only

Run all models locally with Ollama (requires 8–16 GB of VRAM).

#### Env vars

```bash
export AXON_PROVIDER_PROFILE=free       # fallback for non-local tasks
export AXON_PROVIDER_OLLAMA=1           # enable Ollama routing
export AXON_OLLAMA_LOCAL_HOST=http://127.0.0.1:11434
```

#### Supported models

```bash
# Lightweight compression and classification
ollama pull phi3:mini

# or for higher quality:
ollama pull gemma2:26b
```

#### Cost

**$0/month** (hardware only; your GPU/CPU).

---

### Option 3: PAID Profile (Claude via OpenRouter)

Preserve D2 tiering (Haiku → Sonnet → Opus) via OpenRouter, unified billing.

#### Env vars

```bash
export AXON_PROVIDER_PROFILE=paid
export OPENROUTER_API_KEY=sk_or_your_key
```

#### Cost

Claude Haiku (trivial): ~$0.0008/1K tokens
Claude Sonnet (analysis): ~$0.009/1K tokens
Claude Opus (architecture): ~$0.045/1K tokens

See [`docs/decisions/dec-106-routing-profiles.md`](decisions/dec-106-routing-profiles.md) for details.

---

## Register AXON with Claude Code

### Method 1: CLI (Recommended)

```bash
# Add the AXON MCP server to Claude Code
claude mcp add axon -- axon serve
```

This writes to your global `.claude/settings.json` and registers the server.

### Method 2: Manual (.claude/settings.json)

Create or edit `.claude/settings.json` in your project root:

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

Or globally in `~/.claude/settings.json`:

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

---

## MCP Tools Available

Once registered, the following tools are available in Claude Code (under the Claude Code subscription, no per-call charge):

| Tool | Purpose | Risk |
|---|---|---|
| `ask` | Unified entry point: detects context, retrieves relevant code, compresses context, returns ready prompts | read |
| `search_code` | Semantic search over indexed codebase | read |
| `get_session_memory` | Retrieve session notes and recent decisions for a project | read |
| `get_dependencies` | Get dependency graph for a symbol/class | read |
| `get_adrs` | Retrieve architecture decision records for a project | read |
| `save_adr` | Save a new architectural decision | write |
| `axon_get_context` | Recall compact project context (recent decisions) | read |
| `axon_capture` | Capture a draft decision into AXON's store | write |
| `axon_search` | Search captured decisions by summary text | read |
| `axon_handoff` | Produce a handoff brief for another agent | read |
| `axon_export_now` | Export decisions as ADR + architecture docs to Obsidian vault | destructive |
| `axon_validation_stats` | Aggregate decision verification pass rate | read |
| `axon_health` | Report health of each AXON subsystem | read |

---

## Quick Start: 3 Steps

### Step 1: Install AXON

```bash
git clone https://github.com/sammyjdev/axon.git
cd axon
pip install -e .
```

### Step 2: Set Profile and Env Vars

Choose one:

**FREE (default)**:
```bash
export AXON_PROVIDER_PROFILE=free
export GROQ_API_KEY=gsk_...
export NVIDIA_NIM_API_KEY=nvapi-...
```

**LOCAL (Ollama)**:
```bash
export AXON_PROVIDER_OLLAMA=1
export AXON_OLLAMA_LOCAL_HOST=http://127.0.0.1:11434
```

**PAID (OpenRouter)**:
```bash
export AXON_PROVIDER_PROFILE=paid
export OPENROUTER_API_KEY=sk_or_...
```

### Step 3: Register MCP and Index

```bash
# Register the server
claude mcp add axon -- axon serve

# Initialize your repo (installs hooks, indexes code)
cd /path/to/your-project
axon init
```

Now open Claude Code on your project — AXON tools are available in the MCP server dropdown.

Try querying:
```
/ask How does the authentication module work?
```

---

## How It Works Under the Hood

1. **Capture**: Git hooks (`post-commit`, `post-push`) and session events (start/end) feed AXON with code changes and decisions.
2. **Index**: Code is chunked, embedded locally (via Qdrant), and stored in SQLite (source of truth).
3. **Retrieve**: MCP tools query SQLite for decisions and Qdrant for semantic code search.
4. **Compress** (optional): Large context is compressed locally via RTK or Caveman (phi3:mini) before returning.
5. **Route**: Calls to cloud models use the active profile (FREE, PAID, or Ollama).

All indexing and retrieval happens on your machine. Only classification and deep reasoning calls route through the cloud (via the chosen profile).

---

## Environment Variables Cheat Sheet

| Variable | Default | Notes |
|---|---|---|
| `AXON_PROVIDER_PROFILE` | `free` | Profile to use: `free`, `paid`, or custom |
| `AXON_PROVIDER_OLLAMA` | `0` | Set to `1` to enable local Ollama routing |
| `AXON_OLLAMA_LOCAL_HOST` | `http://127.0.0.1:11434` | Ollama server address |
| `AXON_ENGINE` | `~/dev/axon` | AXON repository root |
| `AXON_VAULT` | `~/vault` | External Markdown vault for context (optional) |
| `QDRANT_URL` | `http://localhost:6333` | Vector database endpoint |
| `REDIS_URL` | `redis://localhost:6379` | Graph cache endpoint |
| `GROQ_API_KEY` | — | Required for FREE profile |
| `NVIDIA_NIM_API_KEY` | — | Required for FREE profile |
| `OPENROUTER_API_KEY` | — | Required for PAID profile |

---

## Common Workflows

### Session-to-session continuity

Every time you end a session (`axon_session_end`), recent decisions are saved to SQLite. When you resume the project, `axon_get_context` or `ask` recalls them automatically.

### Agent handoff

Push your work and notify the next agent (Codex, Cursor, etc.):
```python
result = await axon_handoff(to_agent="cursor")
# Returns a brief with repo, decisions, and next steps
```

### Architecture docs export

After a design sprint:
```python
await axon_export_now(repo="my-project")
# Exports ADRs and architecture diagram to your Obsidian vault
```

---

## Troubleshooting

### MCP server fails to start

```bash
# Check that axon is installed and the command exists
which axon

# Verify the command runs
axon serve --help
```

### No results from `search_code`

Ensure QDRANT_URL points to a running Qdrant instance and the codebase has been indexed:
```bash
axon init /path/to/your-repo
```

### High latency on first query

The first query may stall while:
- Embedder engine initializes (loads model)
- Vector store connects
- SQLite database is created

Subsequent queries are much faster.

### "Nenhum resultado encontrado" (No results found)

The codebase may not be indexed yet, or the query is too broad. Try:
```bash
axon index /path/to/your-repo
```

---

## Fase 1: Ingest Your Obsidian Notes

AXON recalls **your notes**, not just code. The `ingest-vault` command walks
every `.md` file in your Obsidian vault, extracts entities and relations with
GLYPH's notes schema (`person` / `project` / `concept` / `note` / `source`),
and writes them into the same SQLite graph that `ask` / `get_context` read —
so your notes become retrievable context alongside your code.

### Prerequisites

The notes extractor is **provider-agnostic** (via litellm). Install the GLYPH
library with the document + litellm extras into the same environment as AXON:

```bash
pip install -e ../glyph-kg[document,litellm]   # editable, dev
# or pin the released package once GLYPH publishes a tag
```

### Run it (local Ollama = $0, default)

```bash
# Uses the default local Ollama endpoint (no API key, no cloud spend)
ollama pull llama3
axon ingest-vault                       # auto-discovers the vault (AXON_VAULT / ~/Obsidian)

# Or point at a specific vault / provider:
axon ingest-vault --vault ~/Documents/MyVault
axon ingest-vault --provider litellm --model openrouter/deepseek/deepseek-chat --api-key sk-or-...
axon ingest-vault --provider anthropic --model claude-haiku-4-5   # needs ANTHROPIC_API_KEY
```

The command prints the resolved vault path and the node/edge counts written.
Re-running is safe — nodes upsert by id and edges insert-or-ignore.

### Verify recall

```
/ask What did I decide about <a topic from your notes>?
```

Retrieved segments now include your note entities, not only code symbols.

---

## Fase 2: Measure Recall Quality (GNOMON)

Close the quality loop: GNOMON scores AXON's recall (faithfulness +
context_precision, with a per-case bootstrap confidence interval) using a
**local Ollama judge** — fully offline, $0.

### 1. Expose AXON over an OpenAI-compatible endpoint

```bash
pip install -e ".[http]"            # FastAPI + uvicorn
axon serve-http --port 8765         # POST /v1/chat/completions  (+ GET /health)
```

The endpoint returns the contract GNOMON requires: `choices[0].message.content`,
a top-level `contexts` list, and `usage.total_tokens`.

### 2. Run the gate

```bash
cd ../gnomon-eval
ollama pull llama3                  # the offline judge
gnomon -c config/axon.toml          # points at http://localhost:8765/v1
```

`config/axon.toml` ships a starter dataset (`datasets/second_brain_example/`)
and gate thresholds (`faithfulness ≥ 0.75`, `context_precision ≥ 0.70`).
Replace the dataset with your own `{question, expected_answer,
expected_contexts}` cases to measure recall on **your** vault. See
[`gnomon-eval/docs/EVALUATING_AXON.md`](../../gnomon-eval/docs/EVALUATING_AXON.md).

---

## See Also

- [`docs/USAGE_GUIDE.md`](USAGE_GUIDE.md) — Full CLI reference
- [`docs/VAULT_SETUP.md`](VAULT_SETUP.md) — Obsidian vault bootstrap
- [`docs/decisions/dec-106-routing-profiles.md`](decisions/dec-106-routing-profiles.md) — Profile internals
- [`docs/decisions/dec-109-tool-tracing-and-risk-gating.md`](decisions/dec-109-tool-tracing-and-risk-gating.md) — Tool risk model
