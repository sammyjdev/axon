# `docs/` index

Navigation aid for this directory. See the watch-out at the bottom on why moving a file
under `docs/` has a measurable side effect on the pack-quality ruler.

Read `CLAUDE.md` first; it names `ADR.md`, `ARD.md`, `USAGE_GUIDE.md`, `VAULT_SETUP.md` and
`PROJECT_OVERVIEW.md` as entry points. This index covers the rest.

## Fixed in this pass (2026-09-23)

`USAGE_GUIDE.md` and `ADOPTION.md` (both `CLAUDE.md` entry points) were rewritten against the
current `axon` CLI and D4 (single Postgres + `pgvector`, dec-121). The drift was bigger than a
stale backend name: dec-125 (T6.3, the `axon` unification) deleted `pb ask/index/watch/til/
deep/expand/career/cost` from `axon.cli.pb` outright, and both guides still documented that
surface. Verified replacements, or "removed with no replacement" where none exists — see the
two files for the mapping. In passing: `src/axon/__main__.py`'s `health` command docstring and
`scripts/axon-bootstrap.sh`'s printed reminder both still said "Redis"; both were one-line
fixes, not behavior changes (`axon health` never probed Redis — it probes sqlite, pgvector,
vault, git).

**Open finding, not resolved:** `axon health` still probes `sqlite` (`SessionStore.init()`),
which contradicts `CLAUDE.md` D4's claim that SQLite is "fully retired" (dec-121). Either the
probe is dead code that should be dropped, or D4 overclaims. Needs a read of
`SessionStore.init()`'s actual behavior before either side is changed.

## Root documents

| File | What it is | Status |
|---|---|---|
| `ADOPTION.md` | Recipe for adopting AXON in a new repo | current, entry point — rewritten against the current CLI and D4 |
| `ADR.md` | Public summary of active ADRs | current, entry point |
| `ARD.md` | Active architectural requirements | current, entry point |
| `AUDIT_EXCEPTIONS.md` | Catalog of bare `except Exception:` (Phase 0.4) | stale — counts no longer match the tree, unclear if the narrowing follow-up landed; needs a read, not a move |
| `METRICS.md` | Metrics manifest with source/command | current, actively maintained (through 2026-08-07) — golden-corpus file, do not move without re-measuring |
| `MIGRATION_LOGGING.md` | stdlib→structlog logging plan | stale — target module exists but plan detail unverified; needs a read, not a move |
| `P3_EXTENSION_DOCS.md` | Docs for the P3 extension surfaces | current — all cited modules confirmed present |
| `PROFILES.md` | Operation profiles vs. provider profiles | current — matches dec-106/dec-128 |
| `PROJECT_OVERVIEW.md` | Single-page map of the engine | current, entry point, but its own "Branch state" section is self-flagged stale (last touched 2026-07-09, ~2.5 months and PRs #206-#215 behind) |
| `QUICKSTART_LINUX.md` | Linux setup | current |
| `QUICKSTART_MACOS.md` | macOS setup | current |
| `QUICKSTART_WINDOWS_WSL2.md` | Windows/WSL2 setup | stale — mentions a remote infra host with Qdrant/Redis, not fixed in this pass |
| `ROADMAP.md` | Product roadmap (state as of 2026-06-20) | stale — self-declared active but 3+ months behind, not clearly finished so not archived |
| `SUPPORT_MATRIX.md` | Support matrix by mode/platform | current — matches ARD-009/010 |
| `TIL_AUTOMATION.md` | TIL automation | current (one incidental "qdrant" tag example, not an architecture claim) |
| `USAGE_GUIDE.md` | Day-to-day usage guide | current, entry point — rewritten against the current CLI and D4 |
| `VAULT_SETUP.md` | External vault bootstrap | current, entry point |
| `adoption-diagnosis-2026-08-07.md` | Measured low-adoption diagnosis | historical, measured data — cited 2x in the pack-quality golden corpus, do not move without re-measuring |
| `agent-backlog.md` | Agentic builder loop backlog (18 items, 11 ready / 5 done) | current, actively referenced — cited 10x in the pack-quality golden corpus, do not move without re-measuring |
| `ai-engineering-gap-review.md` | AI-engineering gap review | current — recovered from stash 2026-09-23 (PR #214), undated in the file itself |
| `gpu-setup.md` | Per-machine GPU acceleration setup | current — referenced by the archived `MIGRATION.md` as an active precondition |

**Archived out of the repo (2026-09-23):** `CAPTURE_ROBUSTNESS.md`, `COGNITIVE_AUDIT.md`,
`MIGRATION.md`, `MIGRATION_PYDANTIC.md`, `P3_PLAN.md`, `GLYPH_INTEGRATION_FOLLOWUPS.md`,
`SECOND_BRAIN.md` — all finished or superseded, none referenced by the pack-quality golden
corpus (verified before moving). Moved to `~/backups/discarded-2026-09-23/docs/`, which
carries a `WHY.md` naming the evidence per file. Nothing was deleted.

## Subdirectories

| Path | What it is | Note |
|---|---|---|
| `decisions/` (35 files) | The ADR corpus | Indexed by `ADR.md`, do not touch its index separately |
| `superpowers/specs/` | Design specs | Indexed by the embedder pipeline |
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

## Watch-out: this index does not change the pack-quality ruler

18 of the 111 golden pack-quality cases expect a file under `docs/` (`agent-backlog.md`
leads with 10, `ADR.md` with 4, `decisions/` with 3, `adoption-diagnosis-2026-08-07.md` with
2, `METRICS.md` with 1). The fixture generator drops a case whose expected files left the
tree, so moving or deleting any of those files silently shrinks the corpus and moves the
baseline. Re-measure with `python3 scripts/eval_pack_quality.py` and record the new number in
the same commit as any such move — this pass did, twice:

| | cases | hit rate | coverage |
|---|---|---|---|
| baseline (PR #213) | 111 | 0.640 | 0.429 |
| after archiving 12 files, none golden-referenced | 111 | 0.640 | 0.433 |

The 0.429→0.433 coverage delta is store drift since PR #213 (more decisions indexed), not an
effect of this reorg — the case count and hit rate are unchanged, and none of the archived
files appear in the golden set.
