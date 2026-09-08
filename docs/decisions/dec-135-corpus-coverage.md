# dec-135: coverage is the larger gap in the lessons corpus

- Status: Proposed
- Date: 2026-09-02 (amended 2026-09-03 after external review)
- Relates to: dec-134 (suspended by this measurement), the lesson-delivery
  telemetry (`064898a`), the SessionStart catalogue (`8d04313`)
- Evidence: `scripts/analysis/lesson_replay.py`, `scripts/analysis/delivery_baseline.py`
- **Provenance of the figures, stated up front:** almost none of the numbers
  below come out of committed code. `lesson_replay.py` prints top-k per case and
  ends at `verdict: __` (`lesson_replay.py:173`) for a human to fill in; it
  computes no statistic. The HIT/MISS labels - the primary outcome - exist only
  in this prose and are committed nowhere. The 255-moment mining, the seeded
  sample, the similarity aggregates (0.601, 0.465, 0.513, 0.705), the trigger
  counts and the `tell` re-embedding run have no committed script at all. Only
  the session counts in the last Limitations bullet are code-derived
  (`delivery_baseline.py`). This is the same defect for which dec-134 was
  rewritten, and it is not fixed here - it is disclosed.

## Context

dec-134 proposed a fourth delivery channel for lessons. Its Step 0 - a
retrospective replay against six real mistakes - was supposed to establish that
a delivered lesson helps. It came back 2 of 6 and suspended the channel.

The first reading of that result, including the one recorded in dec-134, was
"the retriever is bad". **Separating the two failure modes puts most of the
weight elsewhere** - though, as the amendments below record, not with the
confidence the first draft of this document claimed.

## The measurement

Six mistakes, all made 2026-09-01, all after the corpus existed. Each queried
with the situation as it looked before the mistake, never with the mistake's own
wording. Read through a standalone vector query, never `LessonStore.search()`,
so the delivery counters stay clean.

Split by whether a useful lesson EXISTED at all:

| case | a relevant lesson exists? | retrieved in top-3? |
|---|---|---|
| zero-valued artifact read as a measured result | yes | **yes** |
| test passed while proving nothing | yes | **yes** |
| mtime-vs-event window bug | no | - |
| `git add -A` swept unrelated files | no | - |
| long-lived process serving pre-install code | no | - |
| rebase kept a guard already moved into the callee | no | - |

Coverage: 2 of 6.

**The ranking column is not stated as a score, and an earlier draft's "ranking:
2 of 2" is withdrawn.** Three reasons, any one sufficient. The "a relevant
lesson exists?" column was filled in by the author *after* seeing the retrieval
output (`lesson_replay.py:20-22,173`), and no blind check of the corpus was made
for the four misses - so every miss becomes a coverage gap by construction, and
the one cell that could falsify the ranking (exists=yes, retrieved=no) had no
protocol by which it could ever be filled. Wilson 95% CI on 2/2 is [34.2%,
100%]: the number carries no information. And a document that concludes no
retriever discriminates cannot lean on the same retriever scoring perfectly.

What survives is an existence claim without a rate attached: **on the four
misses the author found no lesson naming the trap** - his own judgement, not
blind, not independently checked.

### A second sample, drawn without the author choosing

Six hand-picked cases invite the obvious objection: they were chosen by the
person who wanted this conclusion. So a second sample was drawn mechanically.
Self-corrections leave a textual trace, and mining the transcripts for them
since the corpus began (2026-08-22) yields **255 moments across 12 days**. A
seeded random sample of 12 was classified before any retrieval ran: 7 were real
mistakes, 5 were routine in-flight iteration ("fixing the header I wrote
broken").

Those 7 scored **2 of 7 on top-3 retrieval**. That is a hit rate, not a coverage
rate: turning it into coverage would require checking, for each of the five
misses, that no lesson named the trap, and that check was never recorded. The
two columns below therefore measure different things, and the combined row
inherits the weaker of the two.

| sample | measured | rate | Wilson 95% CI |
|---|---|---|---|
| hand-picked | coverage (author-judged) | 2/6 = 33% | [9.7%, 70.0%] |
| randomly sampled from 255 mined self-corrections | top-3 hit rate | 2/7 = 29% | [8.2%, 64.1%] |
| **combined** | mixed - see above | **4/13 = 31%** | **[12.7%, 57.6%]** |

**The selection-bias objection is unanswered, not refuted.** The random sample
is *consistent* with the hand-picked rate, and at this n that is close to no
evidence: two-sided Fisher exact on 2/6 vs 2/7 gives p = 1.0, and the CI on the
difference spans roughly ±50pp. Hand-picked at 5% against random at 80% would
have produced the same "not rejected". Distinguishing 30% coverage from 50% at
80% power needs about 93 cases per arm, not 13. Note also that the gate deciding
which 7 of the 12 sampled moments were "real mistakes" is itself the author's
labelling, so selection can re-enter there.

~31% is the point estimate; the interval it sits in runs from "nearly nothing"
to "more than half".

### How much goes unrecorded

255 self-correction moments in 12 days, of which the sample says roughly 58% are
real mistakes - order of 150 - against 32 lessons recorded in the same window,
which is **roughly one mistake in five**. The point estimate is the whole of it:
7/12 carries a Wilson 95% CI of [32%, 81%], so the real mistakes in the window
are somewhere between **82 and 206**, and the share becoming a lesson is
anywhere from **16% to 39%** - between one in six and one in two and a half.
Directionally this supports decision 2 below; it does not quantify it.

Coverage looks like the binding constraint. Four mistakes made in one working
session had nothing in a 32-lesson corpus that would have prevented them - by
the author's own reading of the corpus - and no retriever can return what was
never written. That the gap is real is not in doubt; its size is.

## What the corpus looks like from the inside

Three properties. The first two do not depend on anyone's labelling; the third
does, and says so where it appears. None is re-derivable from committed code.

**Lessons are tightly clustered relative to each other.** Mean pairwise
similarity between different lessons is 0.601, spanning only 0.532 to 0.642. A
ranking over items whose mutual similarities span 0.11 has little dynamic range
to work with.

An earlier draft of this section went further and said queries "sit outside the
blob", because query-lesson similarity (0.44-0.58) runs below lesson-lesson
similarity (0.601). **That comparison is invalid and the claim is withdrawn.**
Measured: mean pairwise similarity among the six queries is **0.465** - the
queries are more spread out than the lessons, and query-lesson similarity sits
squarely inside the query-query range. Two internally-coherent populations of
different genre and length always look like this to a cosine; it says nothing
about the corpus. What survives is the narrow lesson-lesson band above, which
does not depend on any cross-population comparison.

**Trigger vocabulary is bimodal, and trigger matching cannot select uniquely
even in its best case.** 71 distinct triggers over 32 lessons, 49 of them
singletons. The head is abstract enough to be a category rather than a handle -
`claim-about-state` marks 12 of 32 lessons (38%), `measurement` 9, `benchmark`
7. Querying a lesson with its OWN triggers - the best case trigger matching can
have - returns 9.6 lessons on average, and exactly one lesson in 32 is uniquely
identified. Two caveats on how far that carries: 9.6 is a mean, and with one
trigger covering 38% of the corpus a single attractor can drag it (median and
distribution were not recorded); and this is lesson self-matching, an upper
bound by construction - trigger matching was never run against the real queries.

**No absolute top-1 similarity threshold separates these six pairs.** Hit top-1
scores were 0.542 and 0.557; miss top-1 scores were 0.496, 0.528, 0.557 and
0.575. The highest score in the run belongs to a miss, and a hit and a miss tie
exactly at 0.557. Two limits on the scope: the hit/miss grouping is the author's
labelling, so this property is not label-independent - dec-134's claim that it
"depends on no labelling at all" is wrong and is withdrawn here. And only an
absolute top-1 threshold was tested; a margin (top1 - top2) or rank-stability
feature was not.

## A fix that was tried and did not help

Lessons are written retrospectively ("Reached for a fallback default when a
lookup failed..."); queries are prospective ("I am about to..."). Different
genres, which would explain queries landing outside the corpus blob. `tell` is
the one field written prospectively - it is how you notice the situation - so
re-embedding on `tell` alone should close the gap.

It does spread the corpus: mean pairwise similarity drops 0.601 -> 0.513.
Retrieval did not improve: 1 of 6 against 2 of 6. On the "test proved nothing"
case the correct lesson falls from first to second, displaced by an unrelated
one at 0.705 - the highest score anywhere in either run, on a miss.

**Recorded as unsupported, not falsified**, and the count itself is unreliable.
One case flipping in n=6 is p = 1.0 on a two-sided Fisher exact - it is not
evidence of degradation. Worse, the illustration contradicts the count: falling
from first to second is still inside top-3, so that case did not stop being a
hit under the baseline's own criterion. Either the 2 -> 1 came from the other
case, or the counting silently switched from top-3 to top-1 mid-paragraph. The
run is not committed anywhere, so this cannot be resolved from the artifact.

The genre-mismatch story is also not falsified by this. Testing it would require
testing genre; re-embedding on `tell` can fail for reasons of its own, short
templated strings embedding poorly among them. What can be said: this particular
fix did not help, and its own motivating evidence (the cross-population
comparison) was already withdrawn above.

## Decision

**Treat coverage as the constraint, and stop guessing at retrievers.** Two
retrievers have been measured (full-text vector, `tell`-only vector) and neither
separated hits from misses on these six cases; trigger matching was analysed,
not measured, and fails to select uniquely even in its own best case. That is
weak evidence against three specific designs, not a proof that ranking is fine -
and the coverage gap is the larger effect regardless. A fourth retriever guessed
from the same six cases is not the missing piece.

Concretely:

1. **Nothing new is built until coverage is measured on more than six cases.**
   Six is enough to suspend a channel; it is not enough to direct curation. The
   replay harness takes cases as data - extending it is adding entries, not
   writing code.
2. **The lesson-writing path is the likelier place for the work.** 4 of 6 real
   mistakes went unrecorded on a day when the corpus grew by several entries,
   which suggests the selection of what becomes a lesson is not tracking what
   actually goes wrong. Suggests, on 6 cases - decision 1 comes first.
3. **Automatic injection needs a threshold this data does not offer.** No
   absolute top-1 similarity threshold separates these six hand-labelled pairs.
   That is six points, one embedding family, one author of queries, and only the
   absolute-threshold family was tested - so it suspends dec-134 pending a real
   threshold study, rather than settling the question.

## Limitations

- **Almost nothing here is re-derivable.** See the provenance note at the top.
  The primary outcome - the HIT/MISS labels - is committed nowhere, and the
  miner, the sample seed, the similarity aggregates and the `tell` run have no
  script. Anyone re-running `lesson_replay.py` gets top-k output and must label
  it again by hand, against different lesson rows.
- **The author labelled his own corpus, and selection bias is NOT addressed.**
  The random sample is consistent with the hand-picked rate at n = 13, which is
  not the same as reproducing it (Fisher p = 1.0). Labelling bias is untouched:
  whether a lesson "would have prevented" a mistake is judged by the person who
  made the mistake and wrote the lesson. An external labeller was sought and did
  not complete; an external review of the document itself was completed
  2026-09-03 and produced these amendments.
- **HAND and SAMPLED may overlap.** Both draw on the same 12-day window, and the
  hand-picked cases are themselves textual self-corrections, so they should be
  among the 255 mined moments. No de-duplication was done. If a sampled case is
  the same underlying mistake as a hand-picked one under different wording, the
  combined 4/13 counts one event twice as if independent.
- **The queries were written by the author** - retrospectively, knowing the
  outcome. Wording specificity moves retrieval, and this is distinct from the
  labelling bias above.
- **The embedder is not pinned.** Which link of the chain produced these vectors,
  and whether it is reproducible, is not recorded.
- **n = 13, small enough that no rate here is distinguishable from another.**
  The 95% CI on 4/13 runs [12.7%, 57.6%]. The coverage column
  ("does a relevant lesson exist?") is a judgement call by the person who both
  made the mistakes and wrote the lessons. Conservative in one direction - a
  lesson counted only if it names the trap, not if it is topically near - but
  not independent.
- **The 255-moment frame is a keyword miner**, so it sees mistakes that were
  acknowledged IN TEXT and is blind to mistakes never noticed. Coverage against
  unnoticed mistakes could be worse and cannot be measured this way.
- **The corpus is 11 days old** (first lesson 2026-08-22). A young corpus having
  gaps is unremarkable; the finding is about which gaps, not that gaps exist.
  (The mining window is stated as 12 days elsewhere; the two counts are the same
  span rounded differently and neither is load-bearing.)
- **"0 delivered" is not a measurement.** The `retrieved_count` counters were
  created 2026-09-01 with default 0. Independent evidence shows `search_lessons`
  was being called in the preceding 30 days, which is what retires "0
  delivered". **The earlier figure "49 calls" is withdrawn as the wrong unit:**
  the script sets one boolean per transcript file
  (`delivery_baseline.py:81,104`) and reports *sessions with at least one call*
  (`delivery_baseline.py:129-135`) - it never counts calls, and no call count is
  derivable from committed code. Any future statement about delivery must start
  from the counters' creation date.

## What this does not decide

Whether the lesson-writing path should change by adding cases, by changing what
qualifies as a lesson, or by changing how triggers are chosen. That needs the
larger coverage sample from decision 1, and guessing now is what produced two
unsupported retriever hypotheses in one afternoon.

It also does not decide that ranking is adequate. The document's own ranking
evidence was withdrawn above; what remains is that coverage is the larger and
better-supported gap, on a sample too small to rank the two with confidence.
