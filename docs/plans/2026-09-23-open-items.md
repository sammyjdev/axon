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

### 1.1 PR #211 - TypeSafe pilot harness (MERGED 2026-09-23)

Nine commits. Reviewed twice by `zai/glm-5.3-flash` through the zai-cc rail, the arm pinned
as reviewer in forge's `models.json`. Round 1 read the whole branch; round 2 read only the
four commits added after it.

Round 1 found three defects, all of the class this repo calls a lying green:

| # | Defect | Fix |
|---|---|---|
| 1 | `detect_current` short-circuited on the **live** `Decision.status`, which production mutates through `_mark_superseded`. The corpus pinned `older_ts`, `cosine` and `shared_scope` but not `status`, so the same frozen corpus scored differently week to week and the oracle was partly the output of the detector under test. | `older_status` pinned at extraction and passed in explicitly (`0ba3361`). A corpus extracted earlier refuses to load with the re-extraction named. |
| 2 | `weighted_precision` scored a stratum with no positive prediction as `0.0` and weighted it by full population. | The operator's own uncommitted work solved this across the whole module: `wilson_interval(0, 0)`, every zero-denominator field in `_condition_report` and `weighted_precision` return `None`. An agent's narrower fix was backed out in `520613e` in its favour. |
| 3 | Cost zero on a cache hit, and a provider failure cached as a parse failure forever. | `c365239`. |

Round 2 found that `c365239` **overclaimed**: it stopped the failure being cached but
`_record` still counted every `None` score in `failures`, so the report written *during* an
outage published an inflated `parse_failure_rate` and only the next run recovered. Fixed in
`abf7f75`, together with the CLI gate that refused a sample below `MID_TUNING_MINIMUM` and
had no test at all: deleting the whole block left the suite green.

Final gate: 80 passed, `ruff check` clean, CI 12 of 12.

### 1.2 PR #213 - pack-quality ruler, replacing #179 (MERGED 2026-09-23)

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

### 2.1 `rtk trust` in `~/dev/axon` (DONE 2026-09-23)

Trusted. `rtk` commands no longer print the untrusted-filters warning.

### 2.2 The main checkout is on a feature branch (RESOLVED)

`agent/typesafe-pilot-harness` merged via PR #211. The checkout has since moved through
`master` and is now on `docs/reorg-2026-09-23`.

### 2.3 The backup directory, settled

**Done 2026-09-23.** Eleven files whose work is finished were moved to
`~/backups/discarded-2026-09-23/`, which carries a `WHY.md` naming the evidence per file:
the five issue briefs (all five issues re-verified CLOSED), the cloud-arm plan (implemented
in `972df7e` and `31cbf98`), the four forge artifacts of the closed closeout, and the revvo
PNG (nothing references it). Nothing was deleted; that directory is the tombstone.

The four still-live files came into the repository instead:

| File | Where | Why there |
|---|---|---|
| `2026-07-13-instinct-loop-roadmap.md` | `docs/superpowers/specs/` | header still reads "Approved direction, pending distillation into backlog items"; `specs/` is indexed, `plans/` is excluded on purpose |
| `2026-07-13-retrieval-eval-precision-roadmap.md` | `docs/superpowers/specs/` | same header, never distilled |
| `ai-engineering-gap-review.md` | `docs/` | kept at its original path and undated: the file carries no date and inventing one would put a number in the tree that nothing measured |
| `promotion-workbench-style-comparison.html` | `docs/mockups/` | its original path, and a new directory in the tree. `/api/promotion-candidates` exists and the dashboard has no promotion view, so this is pending design |

---

## 2.4 TypeSafe harness follow-ups, registered not scheduled

Accepted at merge on the operator's call. None blocks the harness landing; the first one
blocks trusting a *rerun* of the pilot.

- **T1. The embedding cache cannot detect that it is stale.**
  `data/typesafe-pilot/embeddings.jsonl` (18.8 MB) keys each entry by `decision.id` alone:
  every line is `{"id", "vector"}`, with no model name and no hash of the summary it
  embedded. If `EMBEDDER_MODEL` changes, or a summary is edited in the store, a
  re-extraction silently reuses the old vectors, the cosines move, the strata move and the
  sample moves, while `sampling.json` goes on asserting `seed: 20260922` pre-registered. So
  the central promise of the design, same seed and same population reproduce the same
  sample, is not currently verifiable. The artifacts in the tree today were built on this
  cache, so fixing it means re-embedding the corpus once. Fix: write `{id, model,
  summary_hash}` and refuse an entry that does not match, in the same shape as the
  `older_status` refusal.
- **T2. A shortfall is visible but not signalled.** If the pool lacks enough pairs with
  unique summaries, the refill returns fewer cases than asked without saying so, and
  `sampling.json` records neither `target` nor the sampled total, so there is nothing to
  compare against without opening `strata.json`. Fix: record `target`, `sampled_total` and
  any shortfall.
- **T3. A cache hit is costed at today's price.** `client.py` recomputes `cost_usd` from
  the pinned tokens but multiplies by `PRICE_PER_MTOK_INPUT` as it stands now, so if the
  price changes, rerunning a fully cached measurement prints a different cost from the
  original report. Fix: stamp the price into the cache entry beside the tokens.
- **T4. The judge side still fabricates zeros.** `band_agreement = 0.0` and
  `_wilson -> (0.0, 0.0)` on an empty holdout print as measured agreement. The same defect
  the operator fixed on the supersession side survives on the judge side; `26b9e0f` scoped
  its claim to supersession honestly, so this is an inconsistency rather than an untruth.

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
