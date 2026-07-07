# AXON — Project Overview (agent reference)

> Single-page map of what AXON is, what it can do today, the state of every
> branch, and the development/validation flow. Kept current as a reference for
> coding agents. Last validated: 2026-06-26 against `master` HEAD `556ac2d`.

## What AXON is

AXON is a self-hosted **context engine** for AI coding agents. It captures
architectural decisions at crystallisation points (git commits, session
boundaries), indexes code into a graph + vectors, and surfaces ranked context on
demand through an **MCP server** (stdio) or a `.axon/context.md` file fallback.
Goal: let any agent (Claude Code, Codex, Cursor) resume work with full context.

It is the front layer of a three-part stack:
- **AXON** — cross-agent memory, decision capture, MCP surface (this repo).
- **GLYPH** (`glyph-kg`) — graph-aware retrieval, delegated to since dec-116.
- **rtkx** — reversible context compression (the `rtk` binary).

Stack: Python 3.11+, Pydantic v2 domain models, async I/O, litellm for routing.
MIT-licensed, installed from source (not on PyPI).

## Subsystems (`src/axon/`)

| Package | Purpose |
|---|---|
| `router` | Task classification (TRIVIAL/CODE_ANALYSIS/ARCHITECTURE/DEEP_REASONING) + model selection per profile; budget downgrade, circuit-break. Hosts `compressor.py` (caveman compression) and `llm_backend.py` (litellm kwarg builder). |
| `embedder` | Code chunking (Python/Java/TS/Markdown via tree-sitter), embeddings (fastembed BGE-base 768d), `index_path` pipeline. Structure-aware Markdown chunker is recent (commits `b488f27`+). |
| `store` | Persistence: `SessionStore` (SQLite, source of truth), `GraphStore` (Redis cache), `VectorStore` (Qdrant), `FailureStore`. Postgres repos exist on agent branches (see dec-121). |
| `validation` | LLM-judge scoring of captured Decisions; pass-rate aggregation. `Decision.judged` is the canonical "scored" flag. |
| `adr` | ADR lifecycle: draft pool, rejection audit, commit-signal extraction (dec-110). |
| `recall` | Unified recall: merge + rank (recency × relevance × validation score) + token-budget truncation; soft supersession (dec-115). |
| `mcp` | MCP stdio server (`server.py`); every tool wrapped by `@traced_tool` risk gate. |
| `cli` | `axon` (`__main__.py`), the single CLI entry point (dec-125); re-registers the surviving commands from `cli/pb.py`, which is no longer its own entry point. |
| `code` | Repo indexer (`index_repo`), diff-symbols, resolver. |
| `context` | Context auto-detection, retrieval strategies, GLYPH adapter (`graph_source.py`), rtkx bootstrap. |
| `memory` | mem0/Qdrant semantic memory; session transcript compression. |
| `expansion` | Domain-pack knowledge expansion + candidate **scoring** (a local-role, dec-122). |
| `doctor` | Stack diagnostics, 3 modes: read-only / `--apply` / `--ci` JSON (dec-114). |
| `hooks` | Git hook install + post-commit/push capture; pre-commit framework bridge (dec-113). |
| `observability` | Tracing (`TraceStore`), `@traced_tool` decorator, compliance events, compression telemetry. |
| `pet` | Terminal companion (`axon familiar`) driven by TraceStore activity (dec-119). |
| `policy` | RESTRICTED-context isolation; blocks cloud + writes for `ctx=work` (dec-109). |
| `registry` / `domains` | Local plugin/domain-pack discovery + manifest schema. |
| `resilience` | Circuit breaker + rate limiter (per-minute/day, dec-106). |
| `triggers` | Scope-end detection → fires judge + doc export. |
| `vault` / `obsidian` | TIL promotion, deep-note suggestion; Obsidian discovery/export/import. |
| `watcher` | Filesystem watcher → re-index on change. |
| `http` | Optional OpenAI-compatible server + live token-savings dashboard (`[http]` extra). |
| `portability` | Bundle export/import of config + stores. |
| `benchmark` | Token-savings model, supersession A/B, **model-eval harness** (dec-122), recall regression guard. |
| `core` | Canonical Pydantic v2 models: `Decision`, `Edge`, `Symbol`. |

## CLI surface

**`axon`** (single CLI, dec-125): `init`, `serve`, `serve-http`, `install-hooks`,
`familiar`, `health`, `doctor [--apply|--ci]`, `status`, `gain`, `export`,
`ingest-vault`, `bootstrap` (env/config scaffold, formerly `pb init`), `setup`,
`configure`, `index-dev`, `note`, `session-save`, `scan`, `search`, `rtk*`,
`run`, `git`. Sub-apps: `axon adr {list,add,sync,hook,infer-commit,review,audit,validate-drafts}`,
`axon graph {index,neighbors,path}`, `axon hooks {install,status}`,
`axon pending {drain,recover}`, `axon session {note,save}`,
`axon profile {list,use,show,create,export}`, `axon portability {export,import}`.

## MCP tools (`src/axon/mcp/server.py`)

Risk class enforced by `@traced_tool`. Destructive needs `AXON_ALLOW_DESTRUCTIVE=1`.

| Tool | Risk | Purpose |
|---|---|---|
| `search_code` | read | Semantic code search by ctx/language/token-budget; GLYPH graph expansion. |
| `get_session_memory` | read | Compressed session summaries + notes + decisions for a project. |
| `get_dependencies` | read | Caller/callee subgraph for a symbol. |
| `get_adrs` | read | Stored ADRs; `ctx=work` gated. |
| `save_adr` | write | Persist an ADR. |
| `ask` | read | Unified context entry: detect ctx, retrieve, caveman+rtkx compress. |
| `get_graph_neighbors` / `get_graph_path` / `get_graph_context` | read | Graph navigation (SQLite; `get_graph_context` via GLYPH, dec-116). |
| `restore_context` | read | Reverse rtkx compression from a `[[ccr:<handle>]]` marker. |
| `axon_session_start` / `axon_session_end` | write | Open/close a session; refresh `.axon/context.md`. |
| `axon_capture` / `axon_capture_event` | write | Capture in-session decision / universal event. |
| `axon_get_context` | read | Ranked compact project context. |
| `axon_search` | read | Text search over captured decisions. |
| `axon_handoff` | read | Handoff brief for another agent. |
| `axon_export_now` / `axon_mark_done` | **destructive** | Export ADR/architecture docs to vault. |
| `axon_validation_stats` | read | Judge pass-rate stats. |
| `axon_health` | read | Subsystem health with per-probe timeout. |

## Storage & routing today

**Runtime stack (dec-101, in force on `master`):** SQLite (source of truth,
`~/.axon/axon.db`) + Qdrant (vectors, BGE-base 768d) + Redis (graph cache) + mem0
(semantic memory over Qdrant) + `.axon/context.md` file fallback.

**Migration in flight (dec-121, status: proposed):** consolidate onto a single
**Postgres + pgvector** (port **5434**, container `axon-postgres`) retiring Qdrant
and Redis from the default runtime. GLYPH stays on in-memory `NetworkXStore` fed
from Postgres. Incremental, gated by the recall regression guard (`AXON_RUN_RECALL=1`).
The agent/issue-* branches (below) are the implementation of this migration.

**Routing (dec-106, accepted):** tier shape is fixed (D2); concrete models come
from `AXON_PROVIDER_PROFILE`:

| Tier | `free` (default) | `paid` |
|---|---|---|
| trivial | `groq/llama-3.1-8b-instant` | `openrouter/anthropic/claude-haiku-4` |
| code analysis | `groq/llama-3.3-70b-versatile` | `openrouter/anthropic/claude-sonnet-4` |
| architecture | `nvidia_nim/meta/llama-3.1-70b-instruct` | `openrouter/anthropic/claude-opus-4` |

Local Ollama is opt-in (`AXON_PROVIDER_OLLAMA=1`, default off). `ctx=work` is
never routed to cloud. Rate-limit breaches raise `DENY_RATE_LIMIT` (not a model
failure). Per machine policy: **no local models/Postgres/Langfuse on the Mac** —
cloud free-tier (NIM/Groq) is the default.

## Active initiative — local roles (dec-122)

Two subsystems run on a small instruct model instead of a frontier LLM:
**scoring** (`expansion/scoring.py`, expansion-candidate verdicts) and the
**caveman compressor** (`router/compressor.py`). dec-122 moves both off
hard-wired local Ollama onto hosted **`gpt-oss-120b`**, split by provider:

- **scoring → Groq** (`groq/openai/gpt-oss-120b`) — high RPM suits per-candidate bursts.
- **compressor → Cerebras** (`cerebras/gpt-oss-120b`) — high TPM/TPD suits larger payloads.
- `ctx=work` stays local/blocked, never reaches a hosted provider.

Rationale (from the in-task `benchmark/model_eval.py` harness): `phi3:mini`
dropped 100% of required symbols; `gpt-oss-120b` scored 1.00 on all checks at
~0.7-1.2s; `qwen3:4b` matched quality but 2-40x slower; desktop Ollama had a
KV-cache OOM trap from an unpinned `num_ctx`.

**Status: uncommitted work-in-progress on `feat/axon-local-roles-wiring`** (the
branch sits at the same commit as `master`; all wiring is in the working tree):

| File | Change |
|---|---|
| `router/llm_backend.py` *(new)* | `resolve_litellm_model()` (bare name → `ollama/` prefix; known providers pass through) + `litellm_kwargs()` (ollama gets `api_base`+`num_ctx`; hosted providers get neither). |
| `config/runtime.py` | New fields `scoring_model` / `scoring_num_ctx` (env `AXON_SCORING_MODEL`, `AXON_SCORING_NUM_CTX`). |
| `expansion/scoring.py` | `ollama` → `litellm`; drops `host` param; `_slm_enabled()` enforces dec-106 opt-in; bare `except` → `logger.warning` before heuristic fallback. |
| `router/compressor.py` | `caveman_compress(ctx=...)` + `is_corporate_context()` guard: never sends `ctx=work` to a hosted provider. |
| `mcp/server.py` | `ask` threads `ctx=effective_ctx` into `caveman_compress_guarded`. |

TDD surface: `tests/router/test_llm_backend.py` (4 tests: prefix resolution +
kwargs by provider), `tests/config/test_scoring_config.py` (2 tests: defaults +
env resolution).

## Branch state (vs `master` `556ac2d`) — curated 2026-06-26

Verdicts established with `git cherry` (patch-id), not ahead/behind. Two dead
branches were deleted (their commits were already in `master` by identical
patch-id): `capture-robustness` (8 commits — dec-110/111/112/113/114, all
already landed) and `feat/axon-pet` (0 unique commits).

| Branch | Theme | Status | Action |
|---|---|---|---|
| `feat/axon-local-roles-wiring` | dec-122 wiring (uncommitted) | active, current work | finish → commit → PR |
| `chore/oss-housekeeping` | AGPL-3.0 relicense + `pyproject` target-version fix | **new, salvaged from oss branch** | review → merge to master (license change) |
| `oss-licensing-and-english` | only the PT→EN doc translation is still unmerged | stale (168 behind; covers pre-dec-115 docs) | re-translate fresh, then delete |
| `agent/issue-29` | race-safe lazy pool/repo init (`asyncio.Lock`) | **ESSENTIAL, independent** | rebase + merge first (no overlap) |
| `agent/issue-33` | dedupe SQLite helpers → `sqlite_helpers.py` | **ESSENTIAL** | rebase before #34 |
| `agent/issue-34` | typed `SessionRepository` Protocol + `_session_columns.py` | **ESSENTIAL** | rebase after #33 |
| `agent/issue-30` | versioned PG migration runner (`schema_version`) | **ESSENTIAL, foundational** | rebase before #27 |
| `agent/issue-27-legendary` | idempotent SQLite→PG copy (`ON CONFLICT` natural key) | **ESSENTIAL** | rebase after #30 (move UNIQUE idx into migration SQL) |
| `agent/issue-28` | atomic `end_session` (writable CTE) + non-destructive re-save | **ESSENTIAL** | rebase after #27 |
| `agent/issue-35` | lint debt + Windows-path test fixes + PEP695 revert | **ESSENTIAL** (2 commits, apply as pair) | cherry-pick pair last |

**The `agent/issue-*` batch** is a coordinated, agent-generated set (one GH issue
each, #27-#35) hardening the **Postgres layer** whose base already lives in
`master` (`pg_*_repository.py`, `migrations/*.sql`); dec-121 is still `proposed`.
All seven are ESSENTIAL — none redundant — fixing live race conditions,
non-idempotent copies, duplicated helpers, and untyped Protocols. Five edit
`pg_session_repository.py` and three edit `session_repository.py`, so they
**cannot merge independently**. Recommended rebase order:
`#29 → #33 → #34 → #30 → #27 → #28 → #35`, tests after each. Issues #31
(timestamptz) and #32 (migration validation) are open but have no branch yet.
Note: the `pyproject` py312→py311 fix (in `chore/oss-housekeeping`) removes the
root cause behind #35's PEP695 breakage.

## Development & validation flow

1. **TDD is non-negotiable** (CLAUDE.md): a failing test before production code,
   regression test for bugfixes, testable acceptance criteria for features. Never
   silence a failing test/guardrail to look done.
2. **Validation commands** (prefix with `rtk`):
   ```bash
   rtk pytest tests/ -q
   rtk ruff check
   rtk python3 -m compileall src
   ```
3. **`axon doctor`** (dec-114): read-only by default; `--apply` to fix; `--ci` JSON.
4. **Hooks (dec-113):** AXON never mutates `.git/hooks/` or `core.hooksPath` by
   default; installed explicitly via `axon hooks install --apply`. Points:
   post-commit (signal + L1/L2/L3 gates), pre-push, post-checkout.
5. **ADR capture (dec-110):** inference fires only on commits carrying a signal —
   `arch:`/`decision:` subject prefix or `ADR-Decision: <title>` trailer. No
   signal → `CodeChange` captured, LLM inference skipped.
6. **ADR validation gates (dec-111, SLA < 100ms):** L1 structural/presence →
   L2 lexical rationale-overlap → L3 polarity. Pass → SessionStore; fail →
   `.axon/adr-draft/` (review via `axon adr review`).

## Key invariants (do not break)

- D1: vault data stays out of the engine tree.
- D5: the Java chunker is a release gate — don't weaken its tests to pass changes.
- `Decision.judged: bool` is the canonical scored flag — never use
  `validation_score == 0.0` as a sentinel.
- `ctx=work` is restricted: never access implicitly; write/destructive tools are
  denied (`DENY_RESTRICTED_TOOL_WRITE`, dec-109).
- `SessionStore` must be initialized explicitly with `.init()`.

## Pointers

- Decisions: `docs/decisions/dec-{100..122}.md` · active set: `docs/ADR.md`, `docs/ARD.md`
- Usage: `docs/USAGE_GUIDE.md` · vault bootstrap: `docs/VAULT_SETUP.md`
- Agent guide: `CLAUDE.md` (canonical) · onboarded repos: `~/.claude/axon/ROUTER.md`
