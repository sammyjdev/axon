# AXON Repo Invariants (RULES)

Hard invariants the agentic loop (and any contributor) must respect. These are
distilled from `CLAUDE.md` and the active decisions in `docs/decisions/`. When a
change would violate one of these, STOP and surface it - do not work around it.

## Data / engine separation (D1)

- Vault data lives OUTSIDE this repository (`AXON_VAULT`). Runtime code and
  config live inside it. Never move vault or restricted content into the engine
  tree, docs, or tests.

## Restricted context (dec-109)

- `work` is a restricted context. Never access it implicitly; use explicit
  `ctx=work` only when the task truly requires it.
- Write/destructive MCP tools called with `ctx=work` are denied with
  `DENY_RESTRICTED_TOOL_WRITE`. Downgrade the ctx explicitly - never bypass the
  gate.

## Tool risk + consent (ADR-013 / dec-109)

- Every MCP tool carries a risk class (`read` / `write` / `destructive`) enforced
  by `@traced_tool`. Destructive tools require `AXON_ALLOW_DESTRUCTIVE` truthy;
  the default is deny. Do not paper over the consent gate in tests or scripts.

## Chunker is a release gate (D5)

- The Java chunker is a high-risk subsystem. Structure-aware chunking and fixture
  coverage must stay intact. Do NOT weaken chunker tests to make a change pass.

## Decisions storage (dec-121)

- The relational source of truth is Postgres by default; SQLite is the one-flag
  rollback (`AXON_<CONCERN>_BACKEND=sqlite` / `AXON_DB_BACKEND=sqlite`). A change
  must keep the SQLite rollback working.
- `Decision.judged: bool` is the canonical "scored" flag. NEVER use
  `validation_score == 0.0` as a sentinel for unscored decisions.

## Models (dec-105)

- Domain/data models that are persisted or serialized use Pydantic v2. Internal
  config and in-process value objects may use `dataclass`. Prefer either over
  ad-hoc dicts.

## Safety

- Never commit credentials, tokens, `.env` files, or user/vault data.
- `SessionStore` must be initialized explicitly with `.init()`.
- Investigate failing tests / hooks / checks instead of bypassing them; start
  from a test when changing behavior (bugfixes begin with a regression test).

## Style

- Plain hyphens only - never em or en dashes.
