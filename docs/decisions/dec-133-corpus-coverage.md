# dec-133: the lessons corpus has a coverage problem, not a ranking problem

- Status: Proposed
- Date: 2026-09-02
- Relates to: dec-132 (suspended by this measurement), the lesson-delivery
  telemetry (`064898a`), the SessionStart catalogue (`8d04313`)
- Evidence: `scripts/analysis/lesson_replay.py`, `scripts/analysis/delivery_baseline.py`

## Context

dec-132 proposed a fourth delivery channel for lessons. Its Step 0 - a
retrospective replay against six real mistakes - was supposed to establish that
a delivered lesson helps. It came back 2 of 6 and suspended the channel.

The first reading of that result, including the one recorded in dec-132, was
"the retriever is bad". **Separating the two failure modes says otherwise.**

## The measurement

Six mistakes, all made 2026-09-01/02, all after the corpus existed. Each queried
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

**Ranking, on the cases where an answer existed: 2 of 2.** Coverage: 2 of 6.

### The same rate holds on cases the author did not pick

Six hand-picked cases invite the obvious objection: they were chosen by the
person who wanted this conclusion. So a second sample was drawn mechanically.
Self-corrections leave a textual trace, and mining the transcripts for them
since the corpus began (2026-08-22) yields **255 moments across 12 days**. A
seeded random sample of 12 was classified before any retrieval ran: 7 were real
mistakes, 5 were routine in-flight iteration ("fixing the header I wrote
broken").

Those 7 scored **2 of 7**.

| sample | coverage |
|---|---|
| hand-picked | 2/6 = 33% |
| randomly sampled from 255 mined self-corrections | 2/7 = 29% |
| **combined** | **4/13 = 31%** |

The selection-bias objection does not survive: choosing the cases did not bias
the rate. ~30% coverage is the estimate, on 13 cases.

### How much goes unrecorded

255 self-correction moments in 12 days, of which the sample says roughly 58% are
real mistakes - order of 150 - against 32 lessons recorded in the same window.
**Roughly one mistake in five becomes a lesson.** This is the quantitative form
of decision 2 below, which until now rested on four cases.

The binding constraint is coverage. Four mistakes made in one working session
had nothing in a 32-lesson corpus that would have prevented them, and no
retriever can return what was never written.

## What the corpus looks like from the inside

Three properties, all measured, none dependent on anyone's labelling:

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

**Trigger vocabulary is bimodal and neither mode selects.** 71 distinct triggers
over 32 lessons, 49 of them singletons. The head is abstract enough to be a
category rather than a handle - `claim-about-state` marks 12 of 32 lessons
(38%), `measurement` 9, `benchmark` 7. Querying a lesson with its OWN triggers -
the best case trigger matching can have - returns 9.6 lessons on average, and
exactly one lesson in 32 is uniquely identified.

**Similarity scores do not separate relevant from irrelevant.** Hit top-1 scores
were 0.542 and 0.557; miss top-1 scores were 0.496, 0.528, 0.557 and 0.575. The
highest score in the run belongs to a miss, and a hit and a miss tie exactly at
0.557.

## A fix that was tried and failed

Lessons are written retrospectively ("Reached for a fallback default when a
lookup failed..."); queries are prospective ("I am about to..."). Different
genres, which would explain queries landing outside the corpus blob. `tell` is
the one field written prospectively - it is how you notice the situation - so
re-embedding on `tell` alone should close the gap.

It does spread the corpus: mean pairwise similarity drops 0.601 -> 0.513. **And
retrieval gets worse**: 1 of 6 instead of 2 of 6. On the "test proved nothing"
case the correct lesson falls from first to second, displaced by an unrelated
one at 0.705 - the highest score anywhere in either run, on a miss.

Recorded as a falsified hypothesis. Spreading the corpus in embedding space is
not sufficient, and the genre-mismatch story does not survive its own test.

## Decision

**Treat coverage as the constraint, and stop proposing retrievers.** Two
retrievers have now been measured (full-text vector, `tell`-only vector) and one
analysed (trigger matching); none discriminates, and on the only two cases with
an answer available the original retriever already scored 2 of 2. A third
retriever guess is not the missing piece.

Concretely:

1. **Nothing new is built until coverage is measured on more than six cases.**
   Six is enough to suspend a channel; it is not enough to direct curation. The
   replay harness takes cases as data - extending it is adding entries, not
   writing code.
2. **The lesson-writing path is where the work is.** 4 of 6 real mistakes went
   unrecorded on a day when the corpus grew by 8 entries, which means the
   selection of what becomes a lesson is not tracking what actually goes wrong.
3. **The score-separation failure stands regardless of coverage.** Even with a
   complete corpus, automatic injection needs a threshold, and no threshold
   exists in this data. dec-132 stays suspended on this ground alone.

## Limitations

- **The author labelled his own corpus.** Selection bias is addressed - the
  random sample reproduces the hand-picked rate - but LABELLING bias is not.
  Whether a lesson "would have prevented" a mistake is judged by the person who
  made the mistake and wrote the lesson. An external labeller was sought for this
  document and the review did not complete.
- **n = 13, still small.** The coverage column
  ("does a relevant lesson exist?") is a judgement call by the person who both
  made the mistakes and wrote the lessons. Conservative in one direction - a
  lesson counted only if it names the trap, not if it is topically near - but
  not independent.
- **The 255-moment frame is a keyword miner**, so it sees mistakes that were
  acknowledged IN TEXT and is blind to mistakes never noticed. Coverage against
  unnoticed mistakes could be worse and cannot be measured this way.
- **The corpus is 11 days old** (first lesson 2026-08-22). A young corpus having
  gaps is unremarkable; the finding is about which gaps, not that gaps exist.
- **"0 delivered" is not a measurement.** The `retrieved_count` counters were
  created 2026-09-01 with default 0. Independent evidence shows 49
  `search_lessons` calls in the preceding 30 days. Any future statement about
  delivery must start from the counters' creation date.

## What this does not decide

Whether the lesson-writing path should change by adding cases, by changing what
qualifies as a lesson, or by changing how triggers are chosen. That needs the
larger coverage sample from decision 1, and guessing now is what produced two
falsified retriever hypotheses in one afternoon.
