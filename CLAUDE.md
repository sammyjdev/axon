# AXON Agent Guide

This is the canonical agent context file for contributors working in this
repository. `AGENTS.md` points to this file.

## Project Overview

AXON is a self-hosted context engine for local knowledge retrieval,
context compression, and agent-facing workflows through a CLI (`axon`) and MCP.

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

### D2: Task-based cloud routing

D2 defines the **tiering shape** (trivial → mid → top). The concrete model per
tier is selected by the active provider profile (see dec-106). The PAID profile
preserves D2 verbatim via OpenRouter; the FREE profile substitutes equivalent
free-tier models.

| Task type | PAID profile (D2 verbatim) | FREE profile |
| --- | --- | --- |
| trivial/completion | `openrouter/anthropic/claude-haiku-4` | `groq/llama-3.1-8b-instant` |
| code analysis | `openrouter/anthropic/claude-sonnet-4` | `groq/llama-3.3-70b-versatile` |
| architecture/deep reasoning | `openrouter/anthropic/claude-opus-4` | `nvidia_nim/meta/llama-3.1-70b-instruct` |
| fallback | trivial of the active profile | trivial of the active profile |

Downgrade is task-type-driven (top→mid when Opus budget is exceeded; mid→bottom
when daily budget is exceeded) and works identically under both profiles. See
`docs/decisions/dec-106-routing-profiles.md`.

### D3: Local Ollama defaults (opt-in)

Ollama is **opt-in** as of dec-106 (`AXON_PROVIDER_OLLAMA=1`). When enabled,
the supported models are:

- `phi3:mini`: lightweight compression and local-first workflows
- `gemma4:e4b`: local scoring and classification
- `gemma4:26b`: heavier deep-suggestion workloads on larger hardware

Default profiles (FREE/PAID) never route to Ollama; enable it explicitly for
`ctx=work` or any other path that requires local-only execution.

### D4: Split graph backends (revised by dec-101, being superseded by dec-121)

- SQLite is the source-of-truth for the code graph and decisions.
- Redis is retired (dec-121 Phase 2): the `dep:*` call-graph now lives in the
  Postgres `symbol_deps` table; the rate limiter / circuit breaker are in-memory.
- Mem0 runs vector-only over Qdrant. Neo4j was evaluated and dropped — see
  `docs/decisions/dec-101-revoke-d4-drop-neo4j.md`.

> **Superseded in progress (dec-121):** persistence is consolidating onto a
> single Postgres instance — `pgvector` replaces Qdrant, the relational
> source-of-truth (decisions/ADRs/sessions/graph nodes+edges/file_index) moves
> off SQLite, the Redis `dep:*` call-graph ports to a `symbol_deps` PG table
> (its dead `subgraph:*` cache + Mem0 are dropped). GLYPH keeps graph
> **retrieval** (dec-116/117 stand). Rollout is phased (vector → graph/Redis →
> relational); Phase 1 (vector/Qdrant) and Phase 2 (graph/Redis) have landed, so
> only the relational SQLite source-of-truth (Phase 3) is still pending. See
> `docs/decisions/dec-121-postgres-unified-storage.md` and the phase plans under
> `docs/superpowers/plans/`. Until Phase 3 lands, the SQLite statement above
> remains true for the relational data not yet migrated.

### D5: Chunker quality is a release gate

- The Java chunker is a high-risk subsystem.
- Structure-aware chunking and fixture coverage must remain intact.
- Do not weaken chunker tests to make implementation changes pass.

## Recall ranking

`recall_context` supports **opt-in soft supersession** (dec-115, default off): a
stale decision is demoted (rank × 0.02), never dropped, when a newer decision in
the same scope revises it. Detection = shared scope **and** a confirmed revision
— a revision verb in the newer summary (EN/PT) or a near-duplicate
(cosine ≥ `_NEAR_DUP_THRESHOLD` 0.93). Additive same-area work is intentionally
*not* superseded (a flat cosine floor produced ~90% false positives on real
data). Enable via `recall_context(..., enable_supersession=True,
similarity=make_embedding_similarity(EmbedderEngine()))`; keep it off in
production until reworded-revision recall is validated. See
`docs/decisions/dec-115-supersession-ranking-penalty.md` and
`docs/USAGE_GUIDE.md`.

## Onboarding layers (do not conflate)

Two separate, intentionally non-identical lists govern "which repos AXON knows
about". Do **not** try to unify or sync them:

- **Agent scope** — `~/.claude/axon/ROUTER.md` (global, synced dotfiles). The
  **single source of truth** for which repos Claude should recall/capture in. Any
  onboarded-repo list elsewhere (e.g. the integration plan/spec under
  `docs/superpowers/`) only references this one.
- **Index manifest** — `config/projects.json` (committed, read by
  `src/axon/cli/pb.py` for batch indexing). Per-machine and **multi-machine by
  design**: it carries absolute paths for the machine that authored it (e.g. macOS
  `/Users/...`), so on another machine it is dormant and onboarding happens
  per-repo via `axon init <path>`. Foreign paths here are expected, not stale.

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

Use `rtk` where available. Typical validation commands:

```bash
rtk pytest tests/ -q
rtk ruff check
rtk python3 -m compileall src
```

## RTK Notes

AXON is commonly used with RTK (Rust Token Killer) for compact command
output. Prefix commands with `rtk` when possible; if no specialized filter is
available, RTK passes the command through unchanged.
