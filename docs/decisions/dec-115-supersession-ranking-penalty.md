# dec-115 — Soft supersession via ranking penalty in recall

- Status: accepted
- Date: 2026-06-08
- Supersedes: none. Relates to dec-101 (storage stack) and dec-104
  (event-driven capture).

## Context

A comparison with [EpochDB](https://github.com/jersobh/epochdb) surfaced one
idea worth borrowing for AXON's recall: **state-aware supersession**. EpochDB
tracks `(subject, predicate)` atoms and, when a newer atom shares the same pair
as an older one, penalises the older by a `0.0001x` multiplier — demoting it out
of normal retrieval while keeping it in the lossless history.

AXON already models the *shape* of this: `Decision.status` carries a
`superseded` state and `linked_decisions` records relationships. But nothing
populated those automatically, so `recall_context` could still surface a stale
decision at the top of a result.

Adopting EpochDB itself was rejected: its value (tiered hot/cold storage,
turn-level volume, Z3 constraints, memory forking) targets a problem AXON does
not have — AXON's unit is the coarse-grained `Decision`, at low volume, and the
storage stack is already settled (see dec-101). Importing the *concept* is the
smallest coherent change.

## Decision

Add **opt-in soft supersession** to recall ranking:

1. **Penalty, not deletion.** A superseded decision keeps its place in the
   result; only its rank is multiplied by `_SUPERSESSION_PENALTY = 0.02`. This
   mirrors EpochDB's demote-don't-delete behaviour and preserves AXON's
   lossless, SQLite-source-of-truth model (dec-101). The decision stays fully
   recallable if searched for explicitly.

2. **Detection = scope floor + revision confirmation.** The analogue of
   EpochDB's `(subject, predicate)` pair is *shared scope* — overlapping
   `files`/`symbols`. Sharing scope and clearing a *topical floor* (cosine ≥
   `_SCOPE_SIM_THRESHOLD = 0.82`) is **necessary but not sufficient**: additive
   work in the same area (two features in one module, two a11y fixes in one
   stylesheet) is also topically similar. Supersession is inferred only when the
   revision is *confirmed* — either the newer summary carries a **revision verb**
   (`drop`/`replace`/`migrate`/`substitui`/…, EN+PT; ambiguous verbs like
   `fix`/`corrigir` are excluded), or the pair is a **near-duplicate**
   (cosine ≥ `_NEAR_DUP_THRESHOLD = 0.93`, a reworded restatement). Without a
   similarity seam no automatic supersession is inferred, and a pre-existing
   `status == "superseded"` is always honoured. (Refined after the real-data
   validation below — the flat-floor version produced ~90% false positives.)

3. **Opt-in, default off.** `recall_context(..., enable_supersession=False)`
   leaves the legacy ranking byte-for-byte unchanged. The feature only activates
   when the flag is set *and* a `PairwiseSimilarity` seam is supplied. The MCP
   call sites are unchanged, so default AXON behaviour is identical.

4. **Offline.** The similarity seam is backed by the local `fastembed` embedder
   via `make_embedding_similarity`. Supersession detection makes no cloud calls
   and incurs no API cost or rate limit.

## Validation

A dedicated A/B quality benchmark (`axon.benchmark.supersession`) runs a mixed
gold set — curated anchors drawn from AXON's own decision history (Neo4j →
dec-101, Ollama → dec-106) plus generated variations, including control
scenarios that share a file but address different subjects.

Measured on the committed gold set (flag off → on):

| metric | baseline (off) | supersession (on) |
| --- | --- | --- |
| current_precedence | 0% | 100% |
| mean_stale_ratio (↓) | 1.000 | 0.025 |
| recall_completeness | 100% | 100% |
| false_positive | 0% | 0% |

Baseline ties stale and current at the top (recall cannot tell them apart);
with supersession on, the current decision always wins, the stale one is crushed
to ~2.5% of its rank yet stays present, and control scenarios are untouched.

The merge criterion is encoded as a test
(`tests/benchmark/test_supersession_benchmark.py`): the change ships only while
precedence and completeness hold and false positives stay at zero.

### Real-data validation (why detection was refined)

The synthetic benchmark validates the *ranking mechanism* with a lexical proxy;
it does **not** exercise the production embedding detector. Running the real
fastembed cosine at 0.82 over the live store (`axon` + `PitStopOS`, 67 decisions)
exposed what the synthetic set could not: of **11** pairs flagged, **10 were
false positives** — additive same-area commits (e.g. `dec-064 "cálculo de
comissão"` vs `dec-076 "período + líquido de comissão"`) that share a file and
are topically similar but where the older decision is *not* obsolete. Precision
was ~9%.

The synthetic set scored 0% false positives because its supersession pairs were
near-duplicate revisions and its controls were lexically obvious — it never
modelled the dominant real case (additive-but-topically-similar). Adding the
revision-confirmation step (verb OR near-duplicate) cut the real-data detections
from 11 to 1 (the lone near-duplicate true positive), eliminating all 10
additive false positives with no loss on the labelled true positive.

**Known limitation:** the live store currently contains only one true
supersession, and it is a near-duplicate. So FP suppression is well validated but
TP *recall* on reworded (verb-signalled) revisions is not yet measured on real
data. The feature therefore ships **default-off**; enable it in production only
once labelled reworded-revision cases accrue and `_NEAR_DUP_THRESHOLD` /
the verb list are calibrated against them.

## Consequences

- `recall_context` gains optional parameters (`enable_supersession`,
  `similarity`, `similarity_threshold`, `near_dup_threshold`), all defaulted. No
  schema change, no migration, no new dependency.
- `Decision.status` remains available for manual/explicit marking; the automatic
  penalty does not write it back.
- The benchmark uses a deterministic lexical-Jaccard proxy for the similarity
  seam so it needs no model download; production uses embedding cosine. The
  mechanism under test (scope + agreement → penalty) is identical either way.

## Tuning knobs

- `_SUPERSESSION_PENALTY` (0.02) — how hard a stale decision is demoted.
- `_SCOPE_SIM_THRESHOLD` (0.82) — topical floor: minimum cosine for a pair to be
  a supersession *candidate*.
- `_NEAR_DUP_THRESHOLD` (0.93) — at/above this the pair is a near-duplicate and
  is superseded without needing a revision verb.
- `_REVISION_VERBS` (in `recall/supersession.py`) — the EN/PT verb list that
  confirms a revision below the near-duplicate cut.
