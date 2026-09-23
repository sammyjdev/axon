# `docs/` index

Navigation aid for this directory. Nothing here was moved to build this index — see the
watch-out below on why moving files under `docs/` has a measurable side effect.

Read `CLAUDE.md` first; it names `ADR.md`, `ARD.md`, `USAGE_GUIDE.md`, `VAULT_SETUP.md` and
`PROJECT_OVERVIEW.md` as entry points. This index covers the rest.

## Known issue: stale backend references in two entry points

`USAGE_GUIDE.md` and `ADOPTION.md` still describe Qdrant, Redis and mem0 as active storage
backends. dec-121 retired all three in favor of a single Postgres instance with `pgvector`
(see `CLAUDE.md` D4). `COGNITIVE_AUDIT.md` and `SECOND_BRAIN.md` have the same problem and
are otherwise superseded. This was not fixed in this pass — classifying and indexing first,
content rewrite is a separate decision.

## Root documents

| File | What it is | Status |
|---|---|---|
| `ADOPTION.md` | Recipe for adopting AXON in a new repo | stale — cites retired Qdrant/Redis/mem0 setup |
| `ADR.md` | Public summary of active ADRs | current, entry point |
| `ARD.md` | Active architectural requirements | current, entry point |
| `AUDIT_EXCEPTIONS.md` | Catalog of bare `except Exception:` (Phase 0.4) | stale — counts no longer match the tree |
| `CAPTURE_ROBUSTNESS.md` | Capture-robustness proposal, round 5 (2026-05-27) | historical, superseded — self-declared materialized in dec-110..114 |
| `COGNITIVE_AUDIT.md` | Cognitive-capability audit (2026-05-08) | superseded — cites Qdrant/mem0 |
| `GLYPH_INTEGRATION_FOLLOWUPS.md` | Follow-ups from the GLYPH delegation (dec-116, 2026-06-12) | historical — its one open item confirmed resolved (`0b62d84`) |
| `METRICS.md` | Metrics manifest with source/command | current, actively maintained (through 2026-08-07) |
| `MIGRATION.md` | Qdrant→pgvector blue/green runbook | historical — self-declared "dec-121 Phase 1 complete, kept as historical record" |
| `MIGRATION_LOGGING.md` | stdlib→structlog logging plan | stale — target module exists but plan detail not re-verified |
| `MIGRATION_PYDANTIC.md` | dataclass→Pydantic v2 plan informing dec-105 | historical — dec-105 already materialized |
| `P3_EXTENSION_DOCS.md` | Docs for the P3 extension surfaces | current — all cited modules confirmed present |
| `P3_PLAN.md` | P3 execution plan | historical — self-declared "implemented foundation batch" |
| `PROFILES.md` | Operation profiles vs. provider profiles | current — matches dec-106/dec-128 |
| `PROJECT_OVERVIEW.md` | Single-page map of the engine | current, entry point, but its own "Branch state" section is self-flagged stale (last touched 2026-07-09, ~2.5 months and PRs #206-#215 behind) |
| `QUICKSTART_LINUX.md` | Linux setup | current |
| `QUICKSTART_MACOS.md` | macOS setup | current |
| `QUICKSTART_WINDOWS_WSL2.md` | Windows/WSL2 setup | stale — mentions a remote infra host with Qdrant/Redis |
| `ROADMAP.md` | Product roadmap (state as of 2026-06-20) | stale — self-declared active but 3+ months behind |
| `SECOND_BRAIN.md` | "Second brain" guide via Claude Code | stale, superseded — Qdrant storage, provider set doesn't match current D2 |
| `SUPPORT_MATRIX.md` | Support matrix by mode/platform | current — matches ARD-009/010 |
| `TIL_AUTOMATION.md` | TIL automation | current (one incidental "qdrant" tag example, not an architecture claim) |
| `USAGE_GUIDE.md` | Day-to-day usage guide | current, entry point, but lines 61/72-73/98/122/138 describe Qdrant/Redis/Mem0 as active storage — see known issue above |
| `VAULT_SETUP.md` | External vault bootstrap | current, entry point |
| `adoption-diagnosis-2026-08-07.md` | Measured low-adoption diagnosis | historical, measured data — cited 2x in the pack-quality golden corpus, do not move without re-measuring |
| `agent-backlog.md` | Agentic builder loop backlog (18 items, 11 ready / 5 done) | current, actively referenced — cited 10x in the pack-quality golden corpus, do not move without re-measuring |
| `ai-engineering-gap-review.md` | AI-engineering gap review | current — recovered from stash 2026-09-23 (PR #214), undated in the file itself |
| `gpu-setup.md` | Per-machine GPU acceleration setup | current — referenced by `MIGRATION.md` as an active precondition |

## Subdirectories

| Path | What it is | Note |
|---|---|---|
| `decisions/` (35 files) | The ADR corpus | Indexed by `ADR.md`, do not touch its index separately |
| `superpowers/specs/` | Design specs | Indexed by the embedder pipeline |
| `superpowers/plans/` | Eval-run plans | Deliberately excluded from the index — indexing eval artifacts would leak answers into the measurement instrument |
| `plans/` (7 files) | Closeout and task plans | See table below |
| `mockups/` | UI mockups (HTML) | `promotion-workbench-style-comparison.html`, pending design, no live promotion view yet |
| `launch/` | PyPI launch runbook + beta invite kit | Live plan, not yet executed (repo isn't on PyPI yet) |
| `metrics/` | `ecosystem-health.json` | Output of `scripts/health-check.py`, folded in from the former stray `doc/` tree on 2026-09-23 |
| `assets/` | Images | `axon-hero.png` |

### `plans/`

| File | Status |
|---|---|
| `2026-09-23-open-items.md` | live — the current open-items list, read this first |
| `2026-09-13-dead-dirs-key-analysis.md` | reference data, still cited by the closeout |
| `2026-09-12-axon-isolation-and-keying.md` | finished, zero unticked boxes |
| `2026-09-13-finish-the-keying-flow.md` | finished, zero unticked boxes |
| `2026-09-13-pending-sequence.md` | finished, zero unticked boxes |
| `2026-09-14-closeout-spec-and-tasks.md` | finished, zero unticked boxes |
| `2026-09-13-activity-history-llmops.md` | finished, delivered as PRs #208 and #210 |

Open question, not resolved here: the operator's standing rule is that only PRD/ADR/ARD/
architecture docs are committed and task specs stay untracked, yet `plans/` is committed and
cited by merged commit messages. Deciding whether to keep it in git needs the operator.

## Watch-out: this index does not change the pack-quality ruler

18 of the 111 golden pack-quality cases expect a file under `docs/` (`agent-backlog.md`
leads with 10, `ADR.md` with 4, `decisions/` with 3, `adoption-diagnosis-2026-08-07.md` with
2). The fixture generator drops a case whose expected files left the tree, so moving or
deleting any of those files silently shrinks the corpus and moves the baseline
(currently 0.640 hit rate, 0.429 coverage, 111 cases — PR #213). Re-measure with
`python3 scripts/eval_pack_quality.py` and record the new number in the same commit as any
such move.
