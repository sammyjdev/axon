# Design: Coupling AXON into the global Claude Code flow (Spec A)

Date: 2026-06-19
Status: approved (awaiting review of the written spec)
Scope: **A** (coupling). Provider resilience (gpt-oss multi-provider chain) is **Spec B**, separate.

## Context

AXON exists and is published (master, with the rtkx+GLYPH integration). The goal is to
couple it into the standard Claude Code flow (global CLAUDE.md) so that it starts to
**save documentation, project state across runs, and code** - becoming a cross-session and
cross-agent "second brain".

Constraints and facts that shape the design (verified in the code):
- `axon serve` (MCP, stdio) starts in minimal mode with **SQLite** only; Redis/Qdrant/Ollama
  are lazy and optional (fail only when a tool that depends on them is called).
- Capture/recall/code search work **without an API key**; only `ask` (routing) and the
  ADR judge (on push) touch the cloud.
- `~/.claude` is a **git repo synced** across machines: **never** put secrets there.
- AXON is installed via editable venv; the **code** is resolved by the installed package,
  so `AXON_ENGINE` controls only where the **data** lives - the code does not need to move.
- `.env` is loaded from the **repo root** (`C:\dev\axon\.env`), not from `AXON_ENGINE`.

## Decisions

| Topic | Decision |
|---|---|
| Scope | Full stack: state + docs + **code** (semantic search) |
| Topology | **1 global MCP** + global guidance + capture/index **per-repo**, **single SQLite store** (decisions marked by repo -> cross-repo handoff) |
| Provider (now) | `free` profile (Groq + NVIDIA NIM). Multi-provider resilience = Spec B |
| Capture automation | **git hooks** (commit/push) + **guidance in CLAUDE.md** (no SessionStart hook, no fixed token cost per session) |
| Disk | **Data on D:** (`AXON_ENGINE=D:\axon`); code/venv on `C:\dev\axon` |
| Secrets | `GROQ_API_KEY`/`NVIDIA_NIM_API_KEY` in `C:\dev\axon\.env` (gitignored); never in `~/.claude` |
| Runtime mode | `hybrid-local` (SQLite source-of-truth + Qdrant + cloud routing) |

## Components and changes

### 1. Infrastructure (always-on services)
- `docker compose up -d qdrant redis` (only these two from AXON's `docker-compose.yml`;
  `restart: unless-stopped` survives reboot with Docker Desktop).
- Qdrant volume redirected to **D:** via `docker-compose.override.yml` (gitignored)
  mapping `D:\axon\qdrant` -> `/qdrant/storage` (and Redis -> `D:\axon\redis`).
- Postgres/langfuse/ollama are excluded.

### 2. Global MCP registration - `~/.claude/settings.json` -> `mcpServers.axon`
```json
{
  "command": "C:\\Users\\samde\\dev\\axon\\.venv\\Scripts\\python.exe",
  "args": ["-m", "axon", "serve"],
  "env": {
    "AXON_ENGINE": "D:\\axon",
    "AXON_VAULT": "D:\\axon\\vault",
    "AXON_RUNTIME_MODE": "hybrid-local",
    "AXON_PROVIDER_PROFILE": "free",
    "AXON_EXPANSION_ENABLED": "false"
  }
}
```
No secrets here (keys in `.env`). The repo is detected by the cwd that Claude Code passes
to the server by workspace.

### 3. Guidance in global CLAUDE.md - new `~/.claude/axon/ROUTER.md`
@-imported by `~/.claude/CLAUDE.md` (same pattern as other routers). Content (text,
no secret):
- At the start of work in an onboarded repo: call `axon_get_context` (recall) and/or
  `axon_session_start`.
- When making an architectural/relevant decision: `axon_capture`.
- To search: `search_code` / `get_adrs` / `get_dependencies`.
- When finishing: `axon_session_end` with a summary.
- List the onboarded repos (where these instructions apply).

### 4. Automatic capture via git hooks (per-repo)
`axon install-hooks` in each onboarded repo:
- `post-commit` -> captures decision (draft) from the commit in SQLite.
- `pre-push` -> judge + export of ADR/architecture to the vault.
- `post-merge`/`post-checkout` -> revalidates ADR drafts.
- Never block git (failure is swallowed). Live in `.git/hooks` (not synced).

### 5. Code indexing (per-repo)
`axon init <repo>` (or `axon index <repo>`) -> symbols in SQLite + embeddings in Qdrant.
Enables `search_code`/`ask` over that repo.

### 6. Initial onboarding
`axon init` + `axon install-hooks` on the canonical list of onboarded repos, which lives in
**`~/.claude/axon/ROUTER.md`** (single source - do not re-copy here). (Other repos: on-demand later.)

## Data flow

```
commit/push -(git hook)-> decision/ADR -> SQLite (D:\axon\data\axon.db) -(push)-> vault (D:\axon\vault)
Claude session - axon_get_context -> reads SQLite (recall)
               - search_code/ask  -> reads Qdrant (D:\axon\qdrant) + SQLite
classifier/judge/ask - cloud (free profile: Groq/NIM)   [rest is 100% local]
```

## Units (isolation)

- **Infra** (docker compose + override D:) - starts/stops Qdrant+Redis; independent.
- **MCP registration** (settings.json) - exposes the tools; depends only on venv + env.
- **Guidance** (axon/ROUTER.md) - instructs the agent; plain text, no runtime dependency.
- **Per-repo onboarding** (init + hooks) - capture+index per repo; idempotent, repeatable.

Each unit is testable in isolation (infra: `docker ps`; MCP: `axon_health`; guidance: read by
Claude; onboarding: `axon_get_context`/`search_code` per repo).

## Verification (end-to-end)

1. `docker compose ps` -> qdrant + redis `Up`; storage at `D:\axon\qdrant`.
2. `axon doctor` green (rtkx, caveman, SQLite at `D:\axon\data\axon.db`).
3. MCP: tools `axon_*`/`search_code`/`get_adrs` appear in Claude Code; `axon_health` ok.
4. Commit in an onboarded repo -> `axon_get_context` returns the captured decision.
5. `search_code "<known symbol>"` in an indexed repo returns the snippet (Qdrant).
6. Reopen the project in another session -> `axon_get_context` recovers the previous state.

## User prerequisites
- Create `D:\axon\` (the app creates subfolders data/vault/qdrant).
- Put `GROQ_API_KEY` and `NVIDIA_NIM_API_KEY` in `C:\dev\axon\.env`.
- Docker Desktop installed and set to auto-start.

## Out of scope (Spec B)
Multi-provider resilience chain (Cerebras + Ollama-cloud + automatic fallback,
standardization on `gpt-oss-120b`/`gpt-oss-20b`). Does not block A: capture/recall/code
work without it; it improves `ask`/judge.
