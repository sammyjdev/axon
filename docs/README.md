# `docs/` index

Navigation aid for this directory.

`CLAUDE.md`'s own Entry Points section names five files: `README.md`, `VAULT_SETUP.md`,
`USAGE_GUIDE.md`, `ADR.md`, `ARD.md`. `PROJECT_OVERVIEW.md` is a *global* entry point — the
operator's `~/.claude/axon/ROUTER.md` says to read it first in this repo specifically — not
one `CLAUDE.md` itself names. `ADOPTION.md` isn't named as an entry point by either; it's
current and load-bearing (the adoption recipe), just not in that list. This index covers the
rest.

## Fixed in this pass (2026-09-23)

`USAGE_GUIDE.md`, `ADOPTION.md`, `QUICKSTART_LINUX.md`, `QUICKSTART_MACOS.md` and
`SUPPORT_MATRIX.md` were rewritten against the current `axon` CLI and D4 (single Postgres +
`pgvector`, dec-121). The drift was bigger than a stale backend name: dec-125 (T6.3, the
`axon` unification) deleted `pb ask/index/watch/til/deep/expand/career/cost` from
`axon.cli.pb` outright, and all five docs still documented that surface — plus a wrong
provider-profile table (claimed Groq+NVIDIA-NIM; the real `budget` profile is DeepInfra +
OpenRouter fallback, Groq only runs the classifier step, per `src/axon/router/profiles.py`)
and fabricated default rate-limit numbers (there are no defaults — `AXON_<PROVIDER>_MAX_RPM/
RPD` are opt-in and unlimited unless set, per `src/axon/resilience/rate_limiter.py`). Verified
replacements throughout, or "removed with no replacement" where none exists.
dec-138 later removed the router, the provider profiles and their keys, so none of
those docs asks for a provider key any more.

Two one-line source fixes in passing: `src/axon/__main__.py`'s `health` command docstring and
`scripts/axon-bootstrap.sh`'s comments both still said "Redis" / "2 hooks" — `axon health`
never probed Redis (it probes pgvector, vault, git — see below on why sqlite dropped off that
list) and `axon init` installs four hooks (`post-commit`, `pre-push`, `post-merge`,
`post-checkout`), not two.

**Two independent adversarial reviews** (GLM `glm-5.3-flash`, Codex `gpt-5.6-sol`/high) ran
against the first version of this pass and found real defects — the biggest ones: this
`docs/README.md` had made the CLAUDE.md-entry-point claim above wrongly (now fixed), the
archive step below broke 7 live cross-references elsewhere in the repo (now fixed — see next
section), and the provider-profile/rate-limit fabrications above. Both reviews are otherwise
folded into the fixes described here.

**Archiving broke cross-references — fixed.** Moving 7 root docs out of the repo (next
section) orphaned links in files that were never part of this reorg's original file list:
`README.md`'s documentation table (dropped the `SECOND_BRAIN.md` row), `docs/ADR.md`
(de-linked a `CAPTURE_ROBUSTNESS.md` reference to plain text), `docs/agent-backlog.md`
(OPS-1's file list — see below), `docs/decisions/dec-105-...md` (one `MIGRATION_PYDANTIC.md`
mention annotated, the ADR text otherwise left alone on purpose — historical ADRs are not
rewritten), and `docs/plans/2026-09-23-open-items.md` (one `2026-09-14-closeout-...md`
mention annotated). Two more references inside `docs/superpowers/plans/` and
`docs/superpowers/specs/` were left as-is: those are historical/executed planning artifacts
outside the index, lower stakes, and touching them cuts against "historical plans remain
unchanged."

**`docs/agent-backlog.md`'s OPS-1 item, substantially delivered.** That backlog entry
(P1, status "ready") asked for exactly what this pass did to the doc surface, plus the
`axon health` fix below — its acceptance criteria and a 2026-09-23 note are now in the file
itself. One criterion is still open and is *not* a docs fix: see the finding below.

**`axon health`'s vestigial `sqlite` probe, removed (fixed in this pass).**
`SessionStore.init()` is a documented no-op since the Postgres migration (dec-121 Phase 3) —
the old `sqlite: ok` line was unconditionally true and checked nothing. Removed from
`axon_health()` (`src/axon/mcp/server.py`), the `health` command docstring, and
`test_axon_health_reports_subsystems` (now asserts `pgvector`/`vault`/`git` only). `axon
health` now genuinely reports three subsystems, not four. Confirmed via source read (no
consumer of the sqlite branch anywhere) and the full test suite (2337 passed).

**Open finding, not resolved — a bigger code decision, out of scope for this branch:**
`RULES.md` documents an `AXON_DB_BACKEND` / `AXON_<CONCERN>_BACKEND=sqlite` "rollback" flag
that "a change must keep working." Traced it: `_resolve_concern_backend` in
`src/axon/config/runtime.py` validates and stores the string, but **nothing downstream reads
the four `RuntimeConfig` fields it populates** (`fileindex_backend`, `graph_backend`,
`decisions_backend`, `sessions_backend`) — the SQLite repository modules those fields would
have selected are already deleted (`tests/test_no_sqlite.py`). Setting the flag today does
nothing, silently. About 8 test files
(`tests/config/test_{db,decisions,fileindex,graph,sessions}_backend.py`,
`tests/store/test_session_{graph,decisions,sessions}_backend.py`) test the string-resolution
logic itself, not any actual SQLite behavior — so removing the dead machinery means updating
that whole test surface too, which is a real, separately-scoped cleanup, not a docs fix.
Operator decision (2026-09-23): register as a follow-up, don't fold into this branch.
Tracked as [#217](https://github.com/sammyjdev/axon/issues/217). `RULES.md` no longer
promises the rollback; the dead flags themselves are still #217.

## Root documents

| File | What it is | Status |
|---|---|---|
| `ADOPTION.md` | Recipe for adopting AXON in a new repo | current, load-bearing (not a `CLAUDE.md`-named entry point) — rewritten against the current CLI and D4 |
| `ADR.md` | Public summary of active ADRs | current, entry point |
| `ARD.md` | Active architectural requirements | current, entry point |
| `AUDIT_EXCEPTIONS.md` | Catalog of bare `except Exception:` (Phase 0.4) | stale — counts no longer match the tree, unclear if the narrowing follow-up landed; needs a read, not a move |
| `METRICS.md` | Metrics manifest with source/command | current - only current measured claims (2026-11-26) |
| `MIGRATION_LOGGING.md` | stdlib→structlog logging plan | stale — target module exists but plan detail unverified; needs a read, not a move |
| `P3_EXTENSION_DOCS.md` | Docs for the P3 extension surfaces | stale - describes modules that no longer exist (see tests/test_code_vectors_retired.py) |
| `PROFILES.md` | Operating-mode profiles in `axon.toml` | current - provider profiles removed with dec-138 (2026-10-21) |
| `PROJECT_OVERVIEW.md` | Single-page map of the engine | current, global entry point (see intro above), Branch state curated 2026-10-21 |
| `QUICKSTART_LINUX.md` | Linux setup | current — rewritten in this pass (dead `pb` commands, wrong provider-profile keys) |
| `QUICKSTART_MACOS.md` | macOS setup | current — rewritten in this pass (same defects as Linux) |
| `QUICKSTART_WINDOWS_WSL2.md` | Windows/WSL2 setup | current - rewritten 2026-10-21 for dec-138: the remote host needs only `axon-postgres` |
| ROADMAP.md | Public roadmap F0-F3 (2026-11-26) | current |
| `SUPPORT_MATRIX.md` | Support matrix by mode/platform | current — rewritten in this pass (dead `pb` commands, wrong provider-profile keys, fabricated rate-limit defaults) |
| `TIL_AUTOMATION.md` | TIL automation | stale - describes removed commands (pb til, pb deep suggest) |
| `USAGE_GUIDE.md` | Day-to-day usage guide | current, entry point — rewritten against the current CLI and D4 |
| `VAULT_SETUP.md` | External vault bootstrap | stale - describes removed pb commands; not an entry point until rewritten |
| `adoption-diagnosis-2026-08-07.md` | Measured low-adoption diagnosis | historical, measured data |
| `agent-backlog.md` | Agentic builder loop backlog (18 items, 11 ready / 5 done) | current, actively referenced |
| `ai-engineering-gap-review.md` | AI-engineering gap review | current — recovered from stash 2026-09-23 (PR #214), undated in the file itself |
| `gpu-setup.md` | Per-machine GPU acceleration setup | stale - describes the removed embedder (axon.embedder, fastembed) |

**Archived out of the repo (2026-09-23):** `CAPTURE_ROBUSTNESS.md`, `COGNITIVE_AUDIT.md`,
`MIGRATION.md`, `MIGRATION_PYDANTIC.md`, `P3_PLAN.md`, `GLYPH_INTEGRATION_FOLLOWUPS.md`,
`SECOND_BRAIN.md` — all finished or superseded, none referenced by the pack-quality golden
corpus (verified before moving). Moved to `~/backups/discarded-2026-09-23/docs/`; the
tombstone `WHY.md` naming the evidence per file is one level up, at
`~/backups/discarded-2026-09-23/WHY.md`. Nothing was deleted.

## Subdirectories

| Path | What it is | Note |
|---|---|---|
| `decisions/` (37 files) | The ADR corpus | Indexed by `ADR.md`, do not touch its index separately |
| `superpowers/specs/` | Design specs | Historical design specs; the embedder pipeline that indexed them was removed by dec-138 |
| `superpowers/plans/` | Eval-run plans | Deliberately excluded from the index — indexing eval artifacts would leak answers into the measurement instrument |
| `plans/` (2 files) | Live and reference task plans | See table below |
| `mockups/` | UI mockups (HTML) | `promotion-workbench-style-comparison.html`, pending design, no live promotion view yet |
| `launch/` | PyPI launch runbook + beta invite kit | Live plan, not yet executed (repo isn't on PyPI yet) |
| `metrics/` | `ecosystem-health.json` | Output of `scripts/health-check.py`, folded in from the former stray `doc/` tree on 2026-09-23 |
| `assets/` | Images | `axon-hero.png` |

### `plans/`

| File | Status |
|---|---|
| `2026-09-23-open-items.md` | live — the current open-items list, read this first |
| `2026-09-13-dead-dirs-key-analysis.md` | reference data, still cited by the closeout |

Five finished plans (`2026-09-12-axon-isolation-and-keying.md`,
`2026-09-13-finish-the-keying-flow.md`, `2026-09-13-pending-sequence.md`,
`2026-09-14-closeout-spec-and-tasks.md`, `2026-09-13-activity-history-llmops.md`) were
archived out on 2026-09-23 — see the note above.

**`plans/` staying in git is a deliberate exception**, decided 2026-09-23: the operator's
standing rule (only PRD/ADR/ARD/architecture docs are committed, task specs stay untracked)
does not apply here because these are not in-progress task specs — they are closeout records
cited by merged commit messages, which is closer to an architecture decision log than a
working draft. Not revisited unless the directory starts accumulating live, uncommitted-style
specs again.

## Retired: the pack-quality ruler

The pack-quality ruler and the index-vault / index-dev commands it relied on were removed by dec-138. The former section and its numbers are in the git history: git show f898c82d:docs/README.md.
