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

## Proposed by the loop

Candidate invariants surfaced by FORGE reviews. NOT yet enforced - the human
promotes them into a section above after curation.

- **Postgres `ON CONFLICT DO NOTHING` must not return a fake id.** On a dedup
  skip, `RETURNING id` is NULL; never `return result or 0`. Fall back to
  `SELECT id WHERE <natural key>` to return the real existing row id (the
  `save_adr_inner` pattern). Check: a test asserting the SAME id is returned on a
  second identical insert. (FORGE #27)
- **A UNIQUE inline in `CREATE TABLE IF NOT EXISTS` never retrofits an existing
  table.** Add uniqueness via a separate `CREATE UNIQUE INDEX IF NOT EXISTS` (or
  `ALTER TABLE ... ADD CONSTRAINT`) so existing deployments get it too - otherwise
  `ON CONFLICT` fails at runtime on upgrade. Check: a test that runs
  `ensure_schema` against a pre-existing table and confirms the index exists.
  (FORGE #27)
- **DB-constraint behavior must be tested against a real engine, not a fake.**
  A Python fake that simulates ON CONFLICT proves the loop, not the constraint;
  use testcontainers Postgres for dedup/uniqueness assertions. (FORGE #27)
