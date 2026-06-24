# AXON ↔ Claude Code Integration — Implementation Plan (Spec A)

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
>
> **Read first:** `docs/superpowers/specs/2026-06-19-axon-claude-code-integration-design.md` (the approved design). This plan executes Spec A only; Spec B (provider resilience) is a separate cycle.

**Goal:** Wire the AXON MCP server into the global Claude Code workflow so it saves project state, documentation, and code (semantic search) across sessions.

**Architecture:** One global MCP server (`axon serve`, stdio) registered in `~/.claude/settings.json`; a single SQLite store + Qdrant index on disk **D:**; per-repo capture via git hooks and per-repo code indexing; usage guidance added to the global CLAUDE.md. Code/venv stay on **C:**; only data lives on **D:**.

**Tech Stack:** Python 3.11 (AXON, editable venv at `C:\Users\samde\dev\axon\.venv`), Docker (Qdrant + Redis), Claude Code MCP (stdio), Windows + PowerShell.

## Global Constraints

- **Secrets only** in `C:\Users\samde\dev\axon\.env` (gitignored). **Never** put API keys in `~/.claude/` (it is a synced git repo).
- **Data on D:** `AXON_ENGINE=D:\axon` → SQLite at `D:\axon\data\axon.db`, vault at `D:\axon\vault`, Qdrant storage at `D:\axon\qdrant`. Code/venv unchanged on C:.
- Provider: `free` profile (Groq + NVIDIA NIM). Provider resilience is Spec B (out of scope).
- AXON CLI is invoked as `C:\Users\samde\dev\axon\.venv\Scripts\python.exe -m axon <cmd>` (referred to below as **AXON_PY**).
- MCP changes require a **Claude Code restart** to take effect.
- Onboarded repos: **canonical list lives in `~/.claude/axon/ROUTER.md`** (single source of truth). Do not re-copy it here; read it there. Any list inlined below (e.g. the Task 6 script) is a runnable snapshot that **must be kept identical** to that source.

---

### Task 1: Prerequisites — D: dir, `.env` with keys, Docker running

**Files:**
- Create: `C:\Users\samde\dev\axon\.env`
- Verify: `D:\axon\` exists; Docker Desktop running.

**Interfaces:**
- Produces: a loadable `.env` (Groq/NVIDIA keys + D: paths) that every `AXON_PY` call and the MCP server read via `load_dotenv` (repo-root `.env`).

- [ ] **Step 1 (USER): create the data dir**

```powershell
New-Item -ItemType Directory -Force D:\axon | Out-Null
```

- [ ] **Step 2 (USER): write `C:\Users\samde\dev\axon\.env`** with the keys and D: paths (replace the two key values):

```
AXON_ENGINE=D:\axon
AXON_VAULT=D:\axon\vault
AXON_RUNTIME_MODE=hybrid-local
AXON_PROVIDER_PROFILE=free
AXON_EXPANSION_ENABLED=false
GROQ_API_KEY=gsk_REPLACE_ME
NVIDIA_NIM_API_KEY=nvapi-REPLACE_ME
```

- [ ] **Step 3: verify `.env` is gitignored** (must not be committed)

Run: `cd C:\Users\samde\dev\axon; git check-ignore .env`
Expected: prints `.env` (it is ignored). If it prints nothing, add `.env` to `.gitignore` and commit that line.

- [ ] **Step 4 (USER): confirm Docker Desktop is running**

Run: `docker info --format '{{.ServerVersion}}'`
Expected: prints a version number (Docker daemon reachable).

---

### Task 2: Run Qdrant + Redis with storage on D:

**Files:**
- Create: `C:\Users\samde\dev\axon\docker-compose.override.yml`
- Modify: `C:\Users\samde\dev\axon\.gitignore` (add the override)

**Interfaces:**
- Produces: Qdrant on `http://localhost:6333` (storage `D:\axon\qdrant`) and Redis on `localhost:6379` (`D:\axon\redis`), both `restart: unless-stopped`.

- [ ] **Step 1: create `docker-compose.override.yml`** redirecting volumes to D::

```yaml
services:
  qdrant:
    volumes:
      - D:/axon/qdrant:/qdrant/storage
  redis:
    volumes:
      - D:/axon/redis:/data
```

- [ ] **Step 2: gitignore the override**

Append `docker-compose.override.yml` to `C:\Users\samde\dev\axon\.gitignore` (machine-specific path; do not commit).

- [ ] **Step 3: start only qdrant + redis**

Run: `cd C:\Users\samde\dev\axon; docker compose up -d qdrant redis`
Expected: both containers created/started.

- [ ] **Step 4: verify they are up and using D:**

Run: `docker compose ps`
Expected: `qdrant` and `redis` both `running`/`Up`.
Run: `Test-Path D:\axon\qdrant`
Expected: `True`.

---

### Task 3: Initialize AXON data on D: and confirm health

**Files:** none (creates `D:\axon\data\axon.db` at runtime).

**Interfaces:**
- Consumes: `.env` from Task 1 (so `AXON_PY` resolves D: paths).
- Produces: SQLite DB at `D:\axon\data\axon.db`; a green `axon doctor`.

- [ ] **Step 1: run doctor (creates the DB, checks the stack)**

Run: `C:\Users\samde\dev\axon\.venv\Scripts\python.exe -m axon doctor`
Expected: lines include `axon: ok`, `rtkx: ok (...)`, `caveman engine: ok`. (Cloud/keys not required for doctor.)

- [ ] **Step 2: verify the DB landed on D:**

Run: `Test-Path D:\axon\data\axon.db`
Expected: `True`.

---

### Task 4: Register AXON as a global MCP server  ✅ DONE (2026-06-23)

> **CORRECTION (as-built):** the original plan said to add an `mcpServers` block to
> `~/.claude/settings.json`. **That is invalid for this Claude Code version** — the
> settings.json schema has no `mcpServers` field and rejects it on save. User-scoped MCP
> servers are registered with `claude mcp add --scope user`, which writes to
> **`~/.claude.json`** (machine-local, not the synced `~/.claude` dotfiles repo). This was
> already applied correctly and the server reports **Connected**.

**Files:**
- Modify: `C:\Users\samde\.claude.json` (written by `claude mcp add`, **not** by hand). Machine-local; **never** committed to the synced dotfiles repo.

**Interfaces:**
- Produces: the `search_code`/`get_adrs`/`axon_*` MCP tools in Claude Code after restart.

- [x] **Step 1: register the server (user scope, no secrets)** — equivalent of the applied config:

```powershell
claude mcp add axon --scope user `
  -e AXON_ENGINE=D:\axon `
  -e AXON_VAULT=D:\axon\vault `
  -e AXON_RUNTIME_MODE=hybrid-local `
  -e AXON_PROVIDER_PROFILE=free `
  -e AXON_EXPANSION_ENABLED=false `
  -- C:\Users\samde\dev\axon\.venv\Scripts\python.exe -m axon serve
```

- [x] **Step 2: verify the registration**

Run: `claude mcp get axon`
Expected: `Scope: User config`, `Status: ✔ Connected`, command `python.exe -m axon serve`, the 5 env vars. (Confirmed 2026-06-23.)

- [ ] **Step 3 (USER): restart Claude Code** so the `axon_*` tools load into the session. (A session started before registration will not see them. Verification of tools happens in Task 7 after restart.)

---

### Task 5: Add AXON usage guidance to the global CLAUDE.md

**Files:**
- Create: `C:\Users\samde\.claude\axon\ROUTER.md`
- Modify: `C:\Users\samde\.claude\CLAUDE.md` (add an `@axon/ROUTER.md` import line)

**Interfaces:**
- Produces: standing instructions telling Claude when to recall/capture via AXON tools.

- [ ] **Step 1: create `~/.claude/axon/ROUTER.md`**

```markdown
# AXON Router (context continuity)

AXON is registered as an MCP server and stores project state, decisions, and a
code index across sessions. It is active for these onboarded repos:
axon, glyph-kg, rtk, lina, lume, pharos-backend, pharos-frontend, revvo-piloto, Orion-AI.

When working in an onboarded repo:
- **At the start of a task:** call `axon_get_context` (recall recent decisions/state).
  Optionally `axon_session_start` to open a session.
- **When a decision is made** (architecture, approach, tradeoff): call `axon_capture`
  with a one-line summary (and touched files/symbols if known).
- **To find code or prior decisions:** use `search_code`, `get_adrs`, `get_dependencies`.
- **When wrapping up:** call `axon_session_end` with a short summary of what changed.

Git hooks already capture commit/push decisions automatically — do not duplicate
those; use `axon_capture` for in-session decisions that are not yet committed.

Output rule: no em-dashes or en-dashes, only the plain hyphen `-`.
```

- [ ] **Step 2: import it from the global CLAUDE.md**

Add this line to `C:\Users\samde\.claude\CLAUDE.md` (next to the other `@` imports):

```
@~/.claude/axon/ROUTER.md
```

- [ ] **Step 3: verify both exist and the import is present**

Run: `Test-Path C:\Users\samde\.claude\axon\ROUTER.md; Select-String -Path C:\Users\samde\.claude\CLAUDE.md -Pattern 'axon/ROUTER.md'`
Expected: `True` and a matching line.

---

### Task 6: Onboard the repos (git hooks + code index)

**Files:** per repo: installs `.git/hooks/*` and writes symbols→SQLite, embeddings→Qdrant.

**Interfaces:**
- Consumes: Qdrant up (Task 2), `.env` (Task 1).
- Produces: each repo recallable via `axon_get_context` and searchable via `search_code`.

- [ ] **Step 1: onboard each repo with `axon init`** (installs hooks + indexes). Run once per path.

> The `$repos` array below is a **runnable mirror** of the canonical list in
> `~/.claude/axon/ROUTER.md`. If they ever differ, the ROUTER.md list wins — update this
> snapshot to match, never the reverse.

```powershell
$repos = @(
  'C:\Users\samde\dev\axon',
  'C:\Users\samde\dev\glyph-kg',
  'C:\Users\samde\dev\rtk',
  'C:\Users\samde\dev\lina',
  'C:\Users\samde\dev\lume',
  'C:\Users\samde\dev\pharos-backend',
  'C:\Users\samde\dev\pharos-frontend',
  'C:\Users\samde\dev\revvo-piloto',
  'C:\Users\samde\dev\Orion-AI'
)
foreach ($r in $repos) {
  Write-Host "=== axon init $r ==="
  C:\Users\samde\dev\axon\.venv\Scripts\python.exe -m axon init $r
}
```
Expected: each prints hook-install + index summary (symbol count). Hooks on dormant repos (e.g. lina) are harmless — they only fire on commit/push.

- [ ] **Step 2: verify hooks installed (spot check one repo)**

Run: `Test-Path C:\Users\samde\dev\rtk\.git\hooks\post-commit`
Expected: `True`.

- [ ] **Step 3: verify indexing reached Qdrant**

Run: `C:\Users\samde\dev\axon\.venv\Scripts\python.exe -m axon doctor`
Expected: no Qdrant error; (optionally) a collection now exists — `curl http://localhost:6333/collections` lists one.

---

### Task 7: End-to-end verification + commit config

**Files:**
- Commit (USER decision): `~/.claude/settings.json`, `~/.claude/axon/ROUTER.md`, `~/.claude/CLAUDE.md` to the dotfiles repo (NO secrets).

**Interfaces:**
- Consumes: all prior tasks; a restarted Claude Code (Task 4 Step 3).

- [ ] **Step 1: MCP tools present (post-restart)**

In Claude Code, confirm the `axon` MCP server is connected and call `axon_health`.
Expected: health report (SQLite ok; Qdrant ok; Redis ok or lazy).

- [ ] **Step 2: capture → recall round-trip**

In an onboarded repo, make a trivial commit:
```powershell
cd C:\Users\samde\dev\rtk; git commit --allow-empty -m "chore: axon capture smoke test"
```
Then call `axon_get_context` (repo `rtk`).
Expected: the context includes the just-committed decision/commit.

- [ ] **Step 3: code search works**

Call `search_code` with a known symbol from an indexed repo (e.g. `print_with_hint` in rtk).
Expected: a result pointing at the indexed source.

- [ ] **Step 4 (USER): commit the guidance** to the synced dotfiles repo (verify no secrets first)

> **CORRECTION (as-built):** the MCP registration is **not** in `settings.json` — it lives in
> machine-local `~/.claude.json` (see Task 4) and must **never** be committed (absolute
> machine paths + per-machine config). Only the guidance files are dotfiles-syncable.

```powershell
cd C:\Users\samde\.claude
git add CLAUDE.md axon/ROUTER.md
git status   # confirm NO .env, NO .claude.json, no keys staged
git commit -m "feat: add AXON usage router (MCP registered locally via claude mcp add)"
```
Expected: only the two non-secret guidance files staged.

---

## Self-Review (done)

- **Spec coverage:** infra (Task 2), MCP global (Task 4), CLAUDE.md guidance (Task 5), git hooks + index (Task 6), D: data + secrets-in-.env (Tasks 1/3), onboarding 9 repos (Task 6), verification (Task 7). All spec sections mapped.
- **Placeholders:** none (key values are user-supplied secrets, marked REPLACE_ME by design).
- **Consistency:** `AXON_ENGINE=D:\axon`, venv path, and the onboarded-repo list match the spec throughout.

## Notes for the executor
- If `axon init` errors on a repo that is not a git repo, skip its hooks and run only the index command for it; report which repos were skipped (no silent drops).
- Spec B (provider resilience: Cerebras + Ollama-cloud + fallback chain on `gpt-oss-120b`) is intentionally not in this plan.
