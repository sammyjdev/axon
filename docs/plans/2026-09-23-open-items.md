# Open items after the 2026-09 closeout

Single list of what is still open across the axon repo, the claude-skills repo and the
operator's machine, as of 2026-09-23. The closeout plan
(`docs/plans/2026-09-14-closeout-spec-and-tasks.md`) is finished and carries zero unticked
boxes; everything below either arrived after it, or was deliberately carried out of it.

Every line says who it waits on. `OPERATOR` means it cannot be done by an agent: it needs a
merge, an interactive gate, a judgement call about scope, or a deletion. `AGENT` means it is
mechanical and only waits for a turn.

---

## 0. Read this first: one conflict left on `agent/typesafe-pilot-harness`

`~/dev/axon` is checked out on that branch and carries **uncommitted work**: a
pre-registered sampling design (`SAMPLE_SEED`, `MID_TUNING_MINIMUM`, ~227 new lines in
`corpus.py`) and a treatment of undefined metrics across `supersession_eval.py`
(`wilson_interval(0, 0)`, every zero-denominator field in `_condition_report`, and
`weighted_precision` all return `None` instead of a fabricated zero).

An agent pushed to that branch on 2026-09-22 before noticing, and one of its changes
competed with that work. It was backed out on 2026-09-23 (`520613e`): the local version is
the stronger reading, because a population-weighted figure computed over part of the
population still misreports its own basis.

The collision was then **measured**, not predicted, by applying the saved working-tree patch
against the pushed branch. Result, after the backout:

| File | Applies |
|---|---|
| `corpus.py`, `evaluate.py`, `questions.py`, `supersession_eval.py`, `test_supersession_eval.py` | clean |
| `test_corpus.py` | **one conflict**: the pushed side adds `older_status="active"` to the `fake_supersession` fixture, inside the block the local side rewrites. Take the local block and add that one keyword. |

The working tree was saved first, untouched, to
`~/backups/typesafe-uncommitted-20260923T0935.patch` (855 lines).

---

## 1. In flight

### 1.1 PR #211 - TypeSafe pilot harness (OPERATOR: review and merge)

Branch `agent/typesafe-pilot-harness`, 15 new files, +3418. Reviewed 2026-09-22 by
`zai/glm-5.3-flash` through the zai-cc rail, the arm pinned as reviewer in forge's
`models.json`. Verdict: MERGE WITH FIXES, four findings.

Three commits are on the branch. Blocking findings fixed in `0ba3361`:

| # | Defect | Fix |
|---|---|---|
| 1 | `detect_current` short-circuited on the **live** `Decision.status`, which production mutates through `_mark_superseded`. The corpus pinned `older_ts`, `cosine` and `shared_scope` but not `status`, so the same frozen corpus scored differently week to week, and the oracle was partly the output of the detector under test. | `older_status` is pinned at extraction and passed in explicitly. The arm can no longer reach mutable state. |
| 2 | `weighted_precision` scored a stratum with no positive prediction as precision `0.0` and weighted it by full population. On the pilot corpus `mid` and `high` carry 425 of 6740 between them while sampled at one and two cases. | Undefined precision is left out of the average. A stratum that predicted positives and got them wrong still scores a real zero and still counts. |

Measured deflation in the regression test for #2: `0.08` where the honest number is `0.80`.

The two non-blocking findings are fixed too, in `c365239`:

- **Cost on a cache hit** is recomputed from the pinned tokens and price, so two reports of
  the same measurement stop disagreeing about what it cost. Latency stays `0.0` on purpose:
  no time was spent this run, and `cached` already says which kind of row it is.
- **A provider failure is no longer cached.** `_routed` already returns `(None, None)` when
  the call itself failed and a model name beside a `None` score when the reply would not
  parse, so only the first is retried. A real parse failure is deterministic and stays
  cached, which costs no extra calls for the case that matters.

**Migration consequence, stated:** a corpus extracted before 2026-09-22 has no
`older_status` to pin. `evaluate.py` now refuses such a file by name and says to re-extract,
rather than guessing a status and putting the irreproducible number back. The local corpus
at `data/typesafe-pilot/supersession_cases.jsonl` (150 cases) is pre-change and must be
re-extracted before the next run.

Gate on the branch: 71 passed, `ruff check` clean.

### 1.2 PR #213 - pack-quality ruler, replacing #179 (OPERATOR: review and merge)

**Closed 2026-09-23 in favour of #213**, which carries the same work rebased onto master
plus the defect the rebase exposed. No force-push: the original branch
`fix/pack-eval-ruler-precision` is untouched and #179 carries a comment pointing at #213.

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

### 2.3 Four files left in `~/backups/axon-untracked-2026-09-15/`

Recovered from a teleport auto-stash on 2026-09-15 and moved out of the repo, nothing
deleted. Triaged 2026-09-22; **no file has been discarded and none will be without an
explicit go**.

**Done 2026-09-23.** Eleven files whose work is finished were moved to
`~/backups/discarded-2026-09-23/`, which carries a `WHY.md` naming the evidence per file:
the five issue briefs (all five issues re-verified CLOSED), the cloud-arm plan (implemented
in `972df7e` and `31cbf98`), the four forge artifacts of the closed closeout, and the revvo
PNG (nothing references it). Nothing was deleted; that directory is the tombstone.

Four files stay in the original directory because they are still live:

| File | Why |
|---|---|
| `docs/superpowers/specs/2026-07-13-instinct-loop-roadmap.md` | header still reads "Approved direction, pending distillation into backlog items" |
| `docs/superpowers/specs/2026-07-13-retrieval-eval-precision-roadmap.md` | same header, never distilled |
| `docs/ai-engineering-gap-review.md` | conceptual review of AXON as an AI engineering system |
| `docs/mockups/promotion-workbench-style-comparison.html` | `/api/promotion-candidates` exists, the dashboard has no promotion view, so this is pending design and not history |

Where these four should live is still open: `docs/superpowers/specs/` is
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
