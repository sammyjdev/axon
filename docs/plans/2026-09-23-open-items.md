# Open items after the 2026-09 closeout

Single list of what is still open across the axon repo, the claude-skills repo and the
operator's machine, as of 2026-09-23. The closeout plan
(`docs/plans/2026-09-14-closeout-spec-and-tasks.md`) is finished and carries zero unticked
boxes; everything below either arrived after it, or was deliberately carried out of it.

Every line says who it waits on. `OPERATOR` means it cannot be done by an agent: it needs a
merge, an interactive gate, a judgement call about scope, or a deletion. `AGENT` means it is
mechanical and only waits for a turn.

---

## 0. Read this first: a collision on `agent/typesafe-pilot-harness`

`~/dev/axon` is checked out on that branch at `2eab3c0` and carries **uncommitted work**:
a pre-registered sampling design (`SAMPLE_SEED`, `MID_TUNING_MINIMUM`, ~227 new lines in
`corpus.py`) and a rewrite of `weighted_precision` to return `float | None`.

On 2026-09-22 an agent pushed `0ba3361` to the same branch from a worktree, before noticing
that work. Pulling will collide. What to keep, per hunk:

| File / function | Keep |
|---|---|
| `weighted_precision` in `supersession_eval.py` | **the uncommitted local version.** It makes stratum `precision` `float \| None` and returns `None` when any stratum is undefined, rather than averaging over the strata that are defined. A population-weighted figure computed over part of the population is still a figure that misreports its own basis, so this is the stronger reading of the same defect. |
| `detect_current`, `older_status`, `_supersession_case` in `evaluate.py` | **the pushed version.** The uncommitted work does not touch `detect_current`, and the pinned-status fix is orthogonal to the sampling design. |
| `SupersessionCase` construction in `corpus.py` | both sides edited near it. The pushed side adds one line, `older_status=older.status`; the local side rewrote the surrounding sampling. Take the local structure and re-add that one line. |

The lesson is already recorded in agent memory: pre-create a worktree and check for
uncommitted work in the main checkout before pushing to a branch that is checked out there.

---

## 1. In flight

### 1.1 PR #211 - TypeSafe pilot harness (OPERATOR: review and merge)

Branch `agent/typesafe-pilot-harness`, 15 new files, +3418. Reviewed 2026-09-22 by
`zai/glm-5.3-flash` through the zai-cc rail, the arm pinned as reviewer in forge's
`models.json`. Verdict: MERGE WITH FIXES, four findings.

The two blocking findings are fixed on the branch (`0ba3361`, pushed):

| # | Defect | Fix |
|---|---|---|
| 1 | `detect_current` short-circuited on the **live** `Decision.status`, which production mutates through `_mark_superseded`. The corpus pinned `older_ts`, `cosine` and `shared_scope` but not `status`, so the same frozen corpus scored differently week to week, and the oracle was partly the output of the detector under test. | `older_status` is pinned at extraction and passed in explicitly. The arm can no longer reach mutable state. |
| 2 | `weighted_precision` scored a stratum with no positive prediction as precision `0.0` and weighted it by full population. On the pilot corpus `mid` and `high` carry 425 of 6740 between them while sampled at one and two cases. | Undefined precision is left out of the average. A stratum that predicted positives and got them wrong still scores a real zero and still counts. |

Measured deflation in the regression test for #2: `0.08` where the honest number is `0.80`.

Two findings remain open and are NOT fixed, because each one changes a number the harness
publishes and that is the operator's call:

- **Cost and latency are zero on a cache hit** (`client.py:109-110`). The first run writes
  `$X` into `report-supersession.json`, the rerun writes `$0.0`. The tokens and the pinned
  price are both in the cache, so the fix is to recompute rather than report zero.
- **A transient provider failure is cached as a parse failure forever**
  (`judge_eval.py:144,148`). `{"score": None}` is stored like any score, so
  `parse_failure_rate` counts infrastructure as judge instability and a rerun never
  recovers without deleting the cache key by hand.

**Migration consequence, stated:** a corpus extracted before 2026-09-22 has no
`older_status` to pin. `evaluate.py` now refuses such a file by name and says to re-extract,
rather than guessing a status and putting the irreproducible number back. The local corpus
at `data/typesafe-pilot/supersession_cases.jsonl` (150 cases) is pre-change and must be
re-extracted before the next run.

Gate on the branch: 69 passed, `ruff check` clean.

### 1.2 PR #179 - pack-quality ruler (OPERATOR: review, then merge)

Open since 2026-08-31, was `DIRTY`. Rebased onto master on 2026-09-22 in the worktree
`~/dev/axon-worktrees/rebase-179` and pushed as a **new** branch,
`fix/pack-eval-ruler-precision-rebase`. The original `fix/pack-eval-ruler-precision` was
left untouched: updating #179 in place means a force-push over the branch the PR was opened
from, which is the operator's call, not an agent's. Either point #179 at the new branch or
open a fresh PR from it.

One conflict, in the generated golden fixture. Regenerating at the pinned cutoff produced a
**byte-identical** file to the one the PR committed three weeks earlier, which is the
reproducibility claim of the PR proving itself.

**Decision taken, reversible:** master deleted `scripts/axon-backends-start.sh` with the
retired backends (`c0ae6bb`), and the rebase brought back the two cases that expect it.
`build_cases` now takes an explicit `exists` predicate and prunes a file that is no longer
in the tree, on the same principle its docstring already states for unindexable paths:
charging retrieval for a file it cannot reach measures something other than retrieval.

Effect on the ruler, measured:

| | cases | note |
|---|---|---|
| as the PR committed it | 114 | includes `dec-848`, `dec-849` (deleted script), `dec-817` (a path in another repo) |
| after pruning deleted files | 111 | plus `dec-1064` and `dec-1055` pruned from 6 expected files to 2 (the `recall.coverage` modules retired in `e3abe31`) |

Every pruned path was verified absent from the tree. The baseline was re-measured rather
than asserted, with `scripts/eval_pack_quality.py`, store-only arm, `top_k` 8:

| fixture | cases | hit rate | coverage |
|---|---|---|---|
| this branch as first written | 114 | 0.632 | 0.407 |
| unreachable cases dropped | 111 | **0.640** | **0.429** |

The number rose because cases that could only score zero left the denominator. It is the
instrument moving, not retrieval improving, which is the same caveat the parent commit
already carries. Gate: 59 passed in `tests/benchmark`, `ruff check` clean.

### 1.3 O12 gaps carried out of the closeout (AGENT, when scheduled)

From `dec-136` / PRs #208 and #210, recorded and not bugs to fix silently:

- `activity import` and `activity collect` populate sessions only for the AGY `run` path.
  Claude Code and Codex session rows wait on hook wiring that does not exist yet.
- `activity_cursors.byte_offset` is written and never read. Either wire it or drop the
  column, so the schema stops implying a resume path that is carried by the fingerprint
  string instead.
- An old-shape spool payload would be quarantined rather than replayed. None exist on this
  machine; the blast radius is a checkout that ran `activity run` before `104825b`.

---

## 2. Waits on the operator only

### 2.1 `rtk trust` in `~/dev/axon`

Every command in the repo prints a warning that project filters are not trusted.
`.rtk/filters.toml` is the unmodified template: every filter is commented out, so approving
changes no behaviour. It is an interactive security gate and an agent must not route around
it.

```bash
rtk trust
```

### 2.2 The main checkout is on a feature branch

`~/dev/axon` sits on `agent/typesafe-pilot-harness`, not `master`. Any
`pipx install --force ~/dev/axon` from there installs the feature branch. The fixes in 1.1
were pushed from a worktree, so the checkout is behind by one commit and needs a `pull`.

### 2.3 Fifteen files parked in `~/backups/axon-untracked-2026-09-15/`

Recovered from a teleport auto-stash on 2026-09-15 and moved out of the repo, nothing
deleted. Triaged 2026-09-22; **no file has been discarded and none will be without an
explicit go**.

Recommended to discard, work finished:

| File | Why |
|---|---|
| `docs/superpowers/specs/2026-07-18-degrau0-briefs/issue-{62,69,77,78,79}-brief.md` | all five issues CLOSED |
| `docs/superpowers/plans/2026-07-17-cloud-arm-bridge.md` | implemented, `972df7e` and `31cbf98` |
| `forge-sdd-closeout/.forge/sdd/task-{1,2}-{brief,report}.md` | the closeout they belong to is finished |
| `revvo-desktop-hero.png` | belongs to the revvo project, landed here by accident |

Recommended to keep, still live:

| File | Why |
|---|---|
| `docs/superpowers/specs/2026-07-13-instinct-loop-roadmap.md` | header still reads "Approved direction, pending distillation into backlog items" |
| `docs/superpowers/specs/2026-07-13-retrieval-eval-precision-roadmap.md` | same header, never distilled |
| `docs/ai-engineering-gap-review.md` | conceptual review of AXON as an AI engineering system |
| `docs/mockups/promotion-workbench-style-comparison.html` | `/api/promotion-candidates` exists, the dashboard has no promotion view, so this is pending design and not history |

Where the four survivors should live is also a decision: `docs/superpowers/specs/` is
indexed and findable through `search_code`, `docs/superpowers/plans/` is excluded from the
index on purpose, and the vault is outside the repo entirely.

---

## 3. Closed, recorded so it is not re-investigated

- **Docker restart, 2026-09-15 15:51 UTC.** The Postgres container went down for about a
  minute and came back on its `unless-stopped` policy. Up 7 days since, store intact.
- **F1 held in production.** Of the 133 `session_memory` rows written since the fix, zero
  carry a basename key. The 212 historical `samdev` and `maker-bench` rows were left in
  place by decision.
- **Gemini 3.8 campaign.** Measured before the closeout was written
  (`metron/maker-bench/REPORT-38.md`, `bb640e2`). 3.8-medium and 3.7-high are
  indistinguishable, 3.8-low is separated below. No repin; 3.8-medium recorded as a
  fallback candidate.
- **D4 / nested `codex exec`.** NO-GO on codex-cli 0.154.0 under an outer sandbox. forge
  under Codex needs an exec entry point, which stays out of scope.
