# AXON Agent Guide

This is the canonical agent context file for contributors working in this
repository. `AGENTS.md` points to this file.

## Project Overview

AXON is a self-hosted memory of the work done with AI coding agents:
decisions, lessons, session digests and activity history, served through a CLI
(`axon`) and MCP. It does not index or embed code (dec-138).

The repository contains the engine and runtime code. User knowledge lives in an
external Markdown vault, typically configured through:

- `AXON_ENGINE=/path/to/axon`
- `AXON_VAULT=~/vault`

## Entry Points

- [README.md](README.md): public project overview and quick start
- [docs/VAULT_SETUP.md](docs/VAULT_SETUP.md): external vault bootstrap
- [docs/USAGE_GUIDE.md](docs/USAGE_GUIDE.md): CLI workflows
- [docs/ADR.md](docs/ADR.md): active architectural decisions
- [docs/ARD.md](docs/ARD.md): active architectural requirements

## Stable Architectural Decisions

### D1: Data and engine stay separate

- Vault data lives outside this repository.
- Runtime code and configuration live in this repository.
- Do not mix vault content into the engine tree.

### D2, D3: retired

Task-based cloud routing (D2) and the local Ollama models (D3) went with the
router in dec-138: no commit and no MCP tool calls an LLM. The only LLM call
left is the vault hook (`vault/til_promoter`). See
`docs/decisions/dec-138-stop-code-vectors.md`.

### D4: Single Postgres backend (dec-101 fully superseded by dec-121)

dec-121 is **complete** (all three slices landed; ADR accepted 2026-06-29).
Persistence is consolidated on a single PostgreSQL instance:

- **Relational source-of-truth** (decisions/ADRs/sessions/activity history,
  plus FailureStore/OutcomeStore) lives in Postgres. SQLite is fully retired —
  the SQLite repositories, `SessionStore`'s aiosqlite connection, the SQL
  migrations, and the `aiosqlite` dependency are deleted.
- **No vectors** since dec-138: the code index, pgvector, the `symbol_deps` and
  node/edge graphs and GLYPH are gone; lessons are searched with Postgres
  full-text search. Neo4j was evaluated and dropped — see
  `docs/decisions/dec-101-revoke-d4-drop-neo4j.md`.

Guards `tests/test_no_{qdrant,redis,sqlite}.py` and
`tests/test_code_vectors_retired.py` keep the retired backends out. See
`docs/decisions/dec-121-postgres-unified-storage.md` and the phase plans under
`docs/superpowers/plans/`.

## Recall ranking

`recall_context` supports **opt-in soft supersession** (dec-115, default off): a
stale decision is demoted (rank × 0.02), never dropped, when a newer decision in
the same scope revises it. Detection = shared scope **and** a confirmed revision
— a revision verb in the newer summary (EN/PT) or a near-duplicate
(cosine ≥ `_NEAR_DUP_THRESHOLD` 0.93). Additive same-area work is intentionally
*not* superseded (a flat cosine floor produced ~90% false positives on real
data). Enable via `recall_context(..., enable_supersession=True,
similarity=make_embedding_similarity(<embedder>))`; AXON ships no embedder since
dec-138, so the caller brings one. Keep it off in production until
reworded-revision recall is validated. See
`docs/decisions/dec-115-supersession-ranking-penalty.md` and
`docs/USAGE_GUIDE.md`.

## Onboarding layers

`~/.claude/axon/ROUTER.md` (global, synced dotfiles) is the **single source of
truth** for which repos Claude should recall/capture in. Any onboarded-repo list
elsewhere (e.g. the integration plan/spec under `docs/superpowers/`) only
references this one. A repo is onboarded per machine with `axon init <path>`,
which installs the git hooks. The old index manifest (`config/projects.json`)
went with the code index (dec-138).

## Code Conventions

- Python 3.11+ with type hints
- Domain/data models (persisted or serialized) use Pydantic v2; internal
  config and in-process value objects may use `dataclass`. Prefer either over
  ad-hoc dicts. See `docs/decisions/dec-105-migrate-domain-models-to-pydantic.md`.
- Prefer async for I/O-heavy paths
- Add comments only for non-obvious constraints or rationale
- Keep public examples and docs machine-agnostic
- `SessionStore` must be initialized explicitly with `.init()`

## Agent Rules

- Start from tests when changing behavior.
- Bugfixes should begin with a regression test when feasible.
- Features should have testable acceptance criteria before implementation.
- Do not silence failing tests or guardrails to make a change appear complete.
- Prefer the smallest coherent change that satisfies the behavior.

## Restricted Context Rules

- `work` is a restricted context.
- Never access restricted context implicitly.
- Use explicit `ctx=work` only when the task really requires it.
- Do not copy restricted or proprietary material into the repository or public
  documentation.
- Write/destructive MCP tools called with `ctx=work` are denied with
  `DENY_RESTRICTED_TOOL_WRITE` (dec-109). Downgrade the ctx explicitly if
  the action really belongs in a different context — never bypass the
  gate.

## Tool Risk and Audit

- Every MCP tool carries a risk class (`read` / `write` / `destructive`)
  enforced by `@traced_tool`. See ADR-013 / dec-109.
- Destructive tools require `AXON_ALLOW_DESTRUCTIVE` truthy
  (`1`/`true`/`yes`/`on`). The default is deny — do not paper over the
  consent gate in tests or scripts.
- `Decision.judged: bool` is the canonical "scored" flag. Never use
  `validation_score == 0.0` as a sentinel for unscored decisions — that
  conflates a legitimate clamped-to-zero score with the default and
  causes re-judging on every push.

## Safety Rules

- Never commit credentials, tokens, `.env` files, or user data.
- Never move vault content into the engine repository.
- Never weaken isolation around restricted contexts as a shortcut.
- Investigate failing tests, hooks, or checks instead of bypassing them.

## Validation Defaults

Typical validation commands:

```bash
pytest tests/ -q
ruff check
python3 -m compileall src
```
