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

## Onboarding lists (agent scope)

- Two non-identical lists govern "which repos AXON knows about" and must NOT be
  merged: `~/.claude/axon/ROUTER.md` (agent scope, canonical) and
  `config/projects.json` (index manifest, per-machine and multi-machine by
  design). See the "Onboarding layers" note in `CLAUDE.md`.
- Invariant, enforced by `scripts/check_onboarding_drift.py` (gated under
  `tests/scripts`): every repo onboarded on a machine (AXON git hooks installed)
  MUST appear in the canonical ROUTER.md list. The reverse is allowed (a listed
  repo may live on another machine). Onboard a repo -> add it to ROUTER.md.

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
- **A CLI command registered as an alias/import of another function needs a
  behavioral assertion, not just a name-registration check.** `app.command(name)(func)`
  (or `app.add_typer(sub_app, name=...)`) can be silently miswired to the wrong
  underlying function/object, and a test that only checks the command NAME
  appears in the registered set (e.g. `assert name in _registered_command_names()`)
  will not catch it — a mutation swapping which function backs the name stays
  green. Check: at least one test per registered alias/sub-app that invokes it
  (e.g. `--help`) and asserts on output unique to the correct underlying
  function (a distinctive option flag or docstring phrase), not merely that the
  name exists. (FORGE #60)
- **A security-lint suppression (`# noqa`) justified against a function's
  primary call site must be re-checked against every OTHER caller of that
  function.** Trust assumptions can differ per caller: a URL-fetch helper
  suppressed for S310 as "first-party/config-sourced URL" (its main call
  site) can be reused by a second caller that feeds it URLs extracted from
  untrusted remote content (e.g. `<link>` elements parsed from a followed
  feed), turning an accurate suppression into a latent SSRF/file-read gap.
  Check: before finalizing a suppression's justification, grep every caller
  of the suppressed function/line and confirm the trust rationale holds for
  ALL of them, not just the one the maker happened to read first. (FORGE #68)
- **Generated prose (docs, CI step names, commit bodies) needs an explicit
  em/en-dash scan before commit, not just a mental style check.** A maker
  writing new CONTRIBUTING.md/README.md/SECURITY.md prose reached for an
  em-dash mid-sentence despite "Plain hyphens only" being an enforced
  RULES.md invariant the maker's own brief already named - the violation
  wasn't caught until independent review. Check: `grep -n "—\|–" <changed
  doc files>` on every new/modified line (diff against the pre-change file
  to exclude pre-existing, out-of-scope hits) before the maker reports the
  change as done, not left solely to the reviewer to catch. (FORGE #73)
- **Tests that exercise a real dec-110/ADR-inference code path must isolate
  `AXON_DATA_ROOT`.** `axon.adr.draft_pool.write_draft()` resolves its write
  location via `data_root()`, which defaults to `.axon/` relative to the real
  process CWD (correct for actual git hooks, which run with cwd = the repo
  root) - not to any `repo_root`/`cwd` parameter a caller passes in. A test
  that spins up a real git repo with a real `arch:`/`decision:` signal commit
  and calls the real inference pipeline (not mocked - e.g. because a live LLM
  key is configured in the dev environment) will silently write real draft
  `.md` files into whatever `.axon/adr-draft/` the test process's CWD
  resolves to, typically the actual repo or worktree under test, not the
  test's own tmp directory. This pollutes the environment and can break
  unrelated checks in the SAME gate run (observed: it flipped `pb doctor`'s
  `adr.stale_pending` check from ok to FAIL, failing an unrelated CLI test in
  the same pytest run). Check: any test invoking `_scan_pulled_range`,
  `on_commit`, `run_for_head_async`, or another real (non-mocked) call into
  the dec-110/ADR pipeline must set `monkeypatch.setenv("AXON_DATA_ROOT",
  str(tmp_path / ".axon"))` (or otherwise isolate `data_root()`) before
  exercising it. (cloud-arm-bridge plan, task 2, 2026-07-18)
