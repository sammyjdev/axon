# AXON — Project Overview (agent reference)

> Single-page map of what AXON is, what it can do today, the state of every
> branch, and the development/validation flow. Kept current as a reference for
> coding agents. Subsystems, CLI, MCP and storage refreshed 2026-10-15 for
> dec-138 (code vectorization stopped); the Branch state section is stale (see
> its own note) and needs a fresh curation pass.

## What AXON is

AXON is a self-hosted **memory of the work done with AI coding agents**. It
captures decisions and lessons at crystallisation points (signal commits,
explicit captures, session boundaries), keeps session digests and the
operational activity history, and surfaces them through an **MCP server**
(stdio) or a `.axon/context.md` file read at session start. Goal: let any agent
(Claude Code, Codex, Cursor) resume work with the context of earlier sessions.

Since dec-138 (2026-10-15) AXON does not index or embed code: agents read code
with their own tools, and AXON holds what the code cannot say.

Stack: Python 3.11+, Pydantic v2 domain models, async I/O, PostgreSQL.
Apache-2.0-licensed, installed from source (not on PyPI).

## Subsystems (`src/axon/`)

| Package | Purpose |
|---|---|
| `store` | Persistence, all on Postgres (dec-121): `pg_session_repository`, `pg_decision_repository`, `lessons` (full-text lesson search, dec-138), `failure_store`, `outcome_store`, `pending` (spool when the store is down). |
| `adr` | Commit-signal detection (`signal.py`, dec-110), the lesson draft pool fed by the `Lesson:` trailer, retraction detection. |
| `validation` | Pass-rate aggregation over judged Decisions (`axon_validation_stats`). `Decision.judged` is the canonical "scored" flag. |
| `recall` | Unified recall: merge + rank (recency × relevance × validation score) + token-budget truncation; soft supersession (dec-115). |
| `mcp` | MCP stdio server (`server.py`); every tool wrapped by `@traced_tool` risk gate. |
| `cli` | `axon` (`__main__.py`), the single CLI entry point (dec-125); re-registers the surviving commands from `cli/pb.py`, which is no longer its own entry point. |
| `activity` | Operational activity history (dec-136): Claude Code, Codex and agy transcripts, sanitized, in Postgres `activity_*` tables. |
| `memory` | Session transcript parsing and the rule-based session digest (no LLM). |
| `context` | The context buckets (`personal`, `career`, `knowledge`, `saas`, `work`) and their rules. |
| `doctor` | Stack diagnostics, 3 modes: read-only / `--apply` / `--ci` JSON (dec-114). |
| `hooks` | Git hook install + signal-commit capture (a push is not an AXON event); pre-commit framework bridge (dec-113). |
| `observability` | Tracing (`TraceStore`), `@traced_tool` decorator, compliance events, historical compression telemetry for `axon gain`. |
| `policy` | RESTRICTED-context isolation; blocks writes for `ctx=work` (dec-109). |
| `vault` / `obsidian` | TIL promotion (the vault hook, the only LLM call left); Obsidian discovery/export. |
| `http` | Optional dashboard and activity API server (`[http]` extra). |
| `promotion` | Promotion candidates shown on the dashboard. |
| `core` | Canonical Pydantic v2 model: `Decision`; repo identity. |

## CLI surface

**`axon`** (single CLI, dec-125): `init` (install the git hooks in a repo),
`serve`, `serve-http`, `install-hooks`, `health`,
`doctor [--apply|--ci]`, `status`, `gain`, `export`, `bootstrap` (env/config
scaffold, formerly `pb init`), `setup`, `configure`, `note`, `session-save`,
`seed-lessons`, `rekey-repo`, `session-hook`, `compact-hook`. Sub-apps:
`axon adr {list,add,sync}`, `axon hooks {install,status}`,
`axon pending {drain,recover}`, `axon session {note,save}`,
`axon profile {list,use,show,create,export}`,
`axon activity {run,import,collect,export,search,show,backfill}` (dec-136 operational
activity history).

Removed by dec-138: `search`, `sync`, `index-dev`, `index-vault`,
`index-compare`, `index-prune-legacy`, `scan`, `ingest-vault`,
`graph {index,neighbors,path}` and `adr {hook,infer-commit,review,audit,validate-drafts}`.

## MCP tools (`src/axon/mcp/server.py`)

Risk class enforced by `@traced_tool`. Destructive needs `AXON_ALLOW_DESTRUCTIVE=1`;
no tool is destructive since dec-138.

| Tool | Risk | Purpose |
|---|---|---|
| `get_session_memory` | read | Session digests + notes + decisions for a project. |
| `get_adrs` | read | Stored ADRs; `ctx=work` gated. |
| `save_adr` | write | Persist an ADR. |
| `axon_session_start` / `axon_session_end` | write | Open/close a session; refresh `.axon/context.md`. |
| `axon_capture` / `axon_capture_event` | write | Capture in-session decision / universal event. |
| `axon_record_outcome` | write | Record a shipped-work outcome. |
| `axon_record_lesson` / `axon_search_lessons` | write / read | Record a lesson; full-text lesson search (triggers weigh most). |
| `axon_search_conversations` | read | Text search over stored Claude Code / Codex conversations, newest first. |
| `axon_get_context` | read | Ranked compact project context. |
| `axon_search` | read | Text search over captured decisions. |
| `axon_handoff` | write | Handoff brief for another agent. |
| `axon_validation_stats` | read | Judge pass-rate stats over historical scores. |
| `axon_health` | read | Postgres, vault and git health with per-probe timeout. |

## Storage today

**Runtime stack (dec-121, complete — accepted 2026-06-29):** a single
**PostgreSQL** instance (port **5433**, container `axon-postgres`) is the source
of truth for sessions, decisions, ADRs, activity history and the
FailureStore/OutcomeStore. Lessons live in their own database
(`AXON_LESSONS_PG_URL`) and are searched with Postgres full-text search, so no
provider key is needed (dec-138). SQLite, Qdrant, Redis, mem0 and (since
dec-138) pgvector, GLYPH and the code index are retired. Guarded by
`tests/test_no_{sqlite,qdrant,redis}.py` and `tests/test_code_vectors_retired.py`.

No commit and no MCP tool calls an LLM (dec-138). The only LLM call left is the
vault hook (`vault/til_promoter`), through `litellm`. Per machine policy:
Postgres runs on the Linux workstation; the Mac is a thin client and runs
nothing locally.

## Branch state

**Stale — the 2026-06-26 curation below no longer matches reality.** Every
branch it tracked (the `feat/axon-local-roles-wiring` and `chore/oss-housekeeping`
work, the full `agent/issue-27..35` batch) has landed on `master` and no
longer exists, local or remote. `master` now also carries a fresh, unrelated
set of branches (`agent/issue-60`, `agent/issue-63`, plus ~10 remote-only
branches: `chore/retrieval-telemetry-analysis`, `docs/benchmarks-readme-retired`,
`docs/issue-45-embedder-plan`, `docs/stale-launch-posts`,
`feat/continuous-accounting`, `feat/honest-usage-wave1`,
`feat/issue-45-embedder-bge-m3`, `feat/measured-claims-propagation`,
`feat/session-savings-wave2`, and others) that have not been curated. This
section needs a fresh `git cherry` pass before it can be trusted again — treat
it as a to-do, not a status report, until that happens.

## Development & validation flow

1. **TDD is non-negotiable** (CLAUDE.md): a failing test before production code,
   regression test for bugfixes, testable acceptance criteria for features. Never
   silence a failing test/guardrail to look done.
2. **Validation commands**:
   ```bash
   pytest tests/ -q
   ruff check
   python3 -m compileall src
   ```
3. **`axon doctor`** (dec-114): read-only by default; `--apply` to fix; `--ci` JSON.
4. **Hooks (dec-113):** AXON never mutates `.git/hooks/` or `core.hooksPath` by
   default; installed explicitly via `axon hooks install --apply`. Points:
   post-commit and post-merge (a push is not an AXON event).
5. **Commit capture (dec-110, dec-138):** only a commit carrying a signal is
   captured, with no LLM — an `arch:`/`decision:` subject or an
   `ADR-Decision: <title>` trailer becomes a draft Decision, a `Lesson:`
   trailer becomes a lesson draft. A plain commit touches nothing.

## Key invariants (do not break)

- D1: vault data stays out of the engine tree.
- `Decision.judged: bool` is the canonical scored flag — never use
  `validation_score == 0.0` as a sentinel.
- `ctx=work` is restricted: never access implicitly; write/destructive tools are
  denied (`DENY_RESTRICTED_TOOL_WRITE`, dec-109).
- `SessionStore` must be initialized explicitly with `.init()`.

## Pointers

- Decisions: `docs/decisions/dec-{100..122}.md` · active set: `docs/ADR.md`, `docs/ARD.md`
- Usage: `docs/USAGE_GUIDE.md` · vault bootstrap: `docs/VAULT_SETUP.md`
- Agent guide: `CLAUDE.md` (canonical) · onboarded repos: `~/.claude/axon/ROUTER.md`
