# dec-132: delivery is a position in the turn - and a shadow pilot before any injection

- Status: **Superseded by its own Step 0.** The retrieval quality this design
  depends on was measured 2026-09-02 and does not support it; the injection step
  is suspended, not rejected. See Change log.
- Date: 2026-09-01
- Relates to: dec-129 (stable repo identity), dec-131 (ctx partitions retrieval),
  the lesson-delivery telemetry (`064898a`), the summary cap (`54aeba8`),
  the SessionStart lesson catalogue (`8d04313`)

## Context

AXON captures well. Whether it delivers badly is **not established**, and this
document's first three drafts asserted it as if it were.

876 decisions (counted 2026-09-01) and 32 lessons with usable tell/fix. The
lessons corpus is **11 days old** - first lesson 2026-08-22, and 31 of the 32
are `agent-error`. The `retrieved_count` counters landed 2026-09-01 and default
to 0, so "0 delivered" is the *absence of measurement for the past*, not
evidence that no lesson ever reached an agent. Restating it as the latter, which
earlier drafts and one commit message did, is the same error this repo keeps
making: reading a quiet mechanism's silence as a result.

The independent evidence points the other way. In the 30 days to 2026-09-01
there were **49 `search_lessons` tool calls**, each returning up to five
lessons. Lessons have been delivered. What did not exist was the bookkeeping.

So the premise the six-item delivery plan rests on - that the bottleneck is
delivery - is itself unmeasured. A corpus 11 days old, retrieved 49 times, is
not obviously a broken delivery system.

Three deliveries exist, all push at t=0: the recall block, and now a lesson
catalogue in `.axon/context.md` that a SessionStart hook cats. They share one
structural limit - **the need arises mid-conversation, and t=0 is before the
need exists.** By turn 40 the injection is 100K tokens upstream, which is the
same as absent. That argument stands on its own and is not measured here.

### What we know, and what we only thought we knew

`~/.claude/hooks/axon-search-first.py` is a `PreToolUse(Read)` hook installed
2026-08-10 to nudge the agent toward `search_code`. It had a confound - state
keyed by `session_id`, so subagents inherited "already hinted" - fixed
2026-08-25 (`597421b`).

The first draft of this ADR claimed the fix changed nothing, from a table
showing 1.52% -> 1.63%. **That table was wrong three ways**: it bucketed
sessions by file mtime rather than events by their own timestamps; it counted
every `Read` when the hook only acts on code files under `~/dev`; and it
compared a 21-day window against a 7-day one. Recounted on equal 7-day windows
and eligible reads only - **still not a clean estimator**, see below:

| window | eligible Reads | search_code | ratio |
|---|---|---|---|
| 2026-08-18 → 08-25 (pre-fix) | 52 | 5 | 9.62% |
| 2026-08-25 → 09-01 (post-fix) | 204 | 7 | 3.43% |

Opposite direction from the first draft, on 5 and 7 events (z = 1.88, p = 0.06).
The post-fix window is seven days long because that is all the calendar allows.

Even this table is not a conversion rate: the numerator counts `search_code`
tool calls from any session, while the denominator counts eligible Reads, so the
two are drawn from different populations. The engine's own telemetry, which also
sees non-Claude clients, points the other way again (3 pre-fix, 24 post-fix).
Three ways of counting, three directions, all on single-digit-to-low-double-digit
events.

**The correct statement is that the Read hook's effect is unmeasured**, not that
it failed, and this ADR may not lean on it in either direction. A clean estimator
does exist and was not built - the fraction of sessions having at least one
eligible Read that never call `search_code`, pre versus post, which could also
use the 08-10..08-18 events this table discards. Someone who wants to settle the
Read hook should build that; this ADR does not need it settled.

### The comparison that does hold, once counted on the right unit

Every figure below comes from `scripts/analysis/delivery_baseline.py`, committed
with this ADR. The first two drafts quoted numbers nobody could re-derive, and
two of them were wrong the same way - selecting FILES by mtime, then counting
EVENTS by their own timestamp, so events older than the window leaked in from
long-lived transcripts. That produced "1890 prompts across 38 active days" in a
30-day window, which is impossible. Corrected: **1668 prompts across 24 active
dates**, median 58 per active day. The script filters on the event, everywhere.

Re-derived, 30 days to 2026-09-01, `search_lessons`:

| population | unit | rate | 95% CI |
|---|---|---|---|
| main sessions | session | 5/409 = 1.2% | [0.5%, 2.8%] |
| subagent sessions | session | 16/204 = 7.8% | [4.9%, 12.4%] |
| parents that dispatched subagents | **parent session** | 5/20 = 25.0% | [11.2%, 46.9%] |

**The subagent row is not 204 independent trials.** Subagents are dispatched in
batches; one parent dispatched 46 of the 204, and the hits concentrate in it.
The independent unit is the parent session, and there are 20. An earlier draft
quoted z = 3.47, p = 0.0005 off the unclustered figure. **That p-value is wrong
and must not be quoted.** On the clustered unit the direction survives with a
four-times-wider interval, which is the most this evidence supports.

**The unit is not comparable across the two rows either.** A main session is a
human conversation - dozens of prompts over hours. A subagent transcript is one
dispatched task lasting minutes. "At least once per session" is a far easier bar
for the longer unit, so the comparison has no common denominator.

### The mechanism the first draft asserted is false

The first draft said "FORGE's briefs name the call in the task description".
Checked: the maker brief does not contain `search_lessons`. The caller is the
FORGE orchestrator executing its own process document -
`references/task.md:273` ("retrieve **review-craft** lessons first... write them
to a scratch file") and `references/anneal.md:35`. A mandatory workflow step in
a process the agent is following, run by the orchestrator, not an incidental
reminder reaching an agent mid-task.

That distinction is the whole analogy. `UserPromptSubmit` proposes an
**incidental** reminder; the 25% is evidence about **mandatory workflow
compliance**. They are different mechanisms, and the observation motivating this
ADR does not measure the one the ADR proposes.

**The population is also self-referential.** The hits sit in the home directory
and in a `--bench-forge-vs-sdd-*` tree: this is the tool's own development loop
and its benchmarks measuring themselves, not organic use of an unrelated repo.

### The hypothesis

Where an instruction lands relative to the decision it wants to change:

- `SessionStart` lands before the need exists. It decays.
- `PreToolUse(Read)` lands after the agent chose to read - the plan is formed.
- A mandatory process step lands before the agent plans, and shapes it.

This is a hypothesis with **no clean supporting observation**. The one comparison
available measures a different mechanism, on a self-referential population, on
20 independent clusters. The pilot below exists because the reasoning cannot be
settled by the data on hand.

## Decision

**Step 0, which now precedes everything else: establish that a delivered lesson
helps.** Nothing in this ADR, or in the delivery plan it belongs to, has tested
the link that gives every downstream item its value. Building a fourth delivery
channel before that is optimising the distribution of something of unknown
worth.

The test is retrospective and needs no new mechanism. The 31 `agent-error`
lessons record mistakes that were actually made, with dates. For each, take
sessions AFTER its creation, build the query from the situation as it looked
BEFORE the mistake, and check whether that lesson returns in the top-k. It
measures whether the corpus answers the questions actually being asked,
independent of any channel, on data that already exists - and it is falsifiable
this week rather than after 90 days of traffic.

It also gates the rest, in both directions. If the corpus retrieves well, the
mechanism to build is the only one with evidence behind it: a mandatory step in
a process document the agent already follows, which is how FORGE reaches 25%.
If it retrieves badly, no channel fixes it and the work is in the corpus -
triggers, granularity, what is worth recording at all.

### Step 0 ran, 2026-09-02, and it did not validate the design

`scripts/analysis/lesson_replay.py`, committed with this ADR. Six real mistakes,
all made after the corpus existed, queried with the situation as it looked
before each one. Read through its own vector query, never `search()`, so the
delivery counters stay clean.

**Precision@3: 2 of 6** - but see dec-133: split by whether a relevant lesson
existed at all, ranking scored 2 of 2 and COVERAGE scored 2 of 6. The reading
below ("the retriever is bad") was the first and wrong one; the constraint is
what was never written. What survives here unchanged is the score-separation
failure, which suspends this design on its own.

Hits: reading a zero-valued artifact as a measured
result (matched "scored a benchmark arm as a loss on a ZERO-byte diff"), and a
test that passed while proving nothing (matched "tests whose fixture could do
something the real caller cannot"). Misses: an mtime-versus-event window bug, a
`git add -A` that swept unrelated files, a long-lived MCP process serving
pre-install code, and a rebase that kept a guard already moved into the callee.
Four mistakes made the same day with nothing in the corpus that would have
stopped them.

**The finding that matters more than the precision: the similarity score does
not separate relevant from irrelevant.**

| | top-1 similarity |
|---|---|
| HIT cases | 0.542, 0.557 |
| MISS cases | 0.496, 0.528, 0.557, 0.575 |

The ranges overlap completely. The highest score in the run belongs to a miss
(0.575). A hit and a miss score identically at 0.557.

**This falsifies the mechanism this ADR depends on.** Injection at
`UserPromptSubmit` needs a threshold to decide when to fire. No threshold exists
in this data: any cut that admits the 0.557 hit admits the 0.557 miss, and any
cut above 0.575 admits nothing. Threshold-based injection would fire on noise as
often as on signal - which is precisely the wallpaper constraint 2 forbids. The
shadow pilot would have measured a match rate for a matcher already known not to
discriminate.

A related symptom: a few lessons behave as attractors, returning almost
regardless of the query. One appears in the top-3 of cases 1, 5 and 6; another
in 1, 4 and 5.

**Limits of this result.** n = 6, and the author labelled his own corpus. The
labelling was conservative - a lesson counts as a hit only if it names the trap,
not if it is merely topically near - but it is not independent. The score
overlap depends on no labelling at all.

### Consequence: the pilot is suspended

The bottleneck is not the channel. A fourth delivery channel over a retriever at
33% precision with a non-discriminative score delivers noise more efficiently.
The work moves to the corpus and the retriever - trigger granularity, what is
worth recording, and whether trigger matching separates where the embedding does
not. `UserPromptSubmit` returns to the table only if a retriever exists whose
score can carry a threshold.

**Step 1, suspended pending a retriever that discriminates: a shadow
pilot.** Run the
matcher on `UserPromptSubmit`, log every match and its score, and **inject
nothing.** No behaviour change, no risk to any counter, no tokens on the prompt
path beyond the log.

It answers the question that makes or breaks the design and that no amount of
reasoning can: **what fraction of real prompts clears the threshold?** The first
draft assumed 10-20% with no basis, and the whole power calculation - and
therefore the stopping rule - rests on it.

**Why `UserPromptSubmit` and not `SubagentStart`.** Both are mid-conversation
and both land before a plan is formed, so the first draft's claim that
`UserPromptSubmit` is "the only" such point was false. `SubagentStart` is
excluded for a different reason: subagents are the population already at 7.9%,
reached by their briefs, and FORGE owns that channel. The 1.8% population is the
one with headroom and no owner. `SubagentStart` stays available if the pilot
kills this route.

### Constraints, each from a failure this repo has already had

1. **It must not call `LessonStore.search()`.** `search()` credits
   `retrieved_count` - the counter that made "does anything ever read this?"
   answerable, whose 32-lessons/0-deliveries baseline landed hours ago. A
   crediting read at prompt frequency destroys it within a day and leaves a
   number that measures the hook. The SessionStart catalogue avoided this with a
   non-crediting `catalog()`; here it matters more. Injection needs its own
   counter: a pointer shown is not an agent choosing to look.

2. **Silence must be the common case.** Precedent: the ponytail block, 5.3 KB
   injected unconditionally into every SessionStart including pure-analysis
   sessions, with a standing decision about turning it off. Unconditional
   injection becomes wallpaper, and wallpaper is what gets skipped.

3. **It must degrade silently.** `resolve_lessons_dsn()` raises with no fallback
   by design. On the prompt path an exception is in the way of every message.

4. **Latency budget: p95 under 150 ms, measured in the pilot.** A hook on the
   hottest path in the loop needs a number, not an adjective. The pilot reports
   it; exceeding it kills the route regardless of match rate.

## The metric, written before the code

The honest outcome - "the agent avoided the mistake the lesson describes" - is
not automatically measurable, and nothing here should be dressed up as it.

**Pilot (step 1) outcomes**, all descriptive, no verdict attached:
match rate over eligible prompts, score distribution, p95 latency.

**What the pilot cannot tell you, and the manual step that covers it.** Those
three numbers are all frequency and cost. None of them is PRECISION: a score
above threshold says the matcher fired, not that the lesson was relevant to that
prompt. Tuning a threshold on a score whose validity was never checked is how
eligibility gets manufactured - lower it until enough prompts match, and the
match rate looks healthy while the matches are noise.

So the pilot has a fourth output and it is not automatic: **sample 30 matches
spanning the score range, read the prompt and the matched lesson side by side,
and label each relevant / not.** Precision below roughly half means the matcher
is the problem and no threshold fixes it. This is the one part of the pilot a
human has to do, and skipping it makes the other three numbers unreadable.

**Experiment (step 2, only if the pilot justifies it):**

- **Randomise by SESSION, not by prompt** - and by CONVERSATION, not by
  `session_id`. Per-prompt assignment leaks: an injection at turn 3 changes the
  agent's behaviour at turn 5, which may be a held prompt. Per-`session_id`
  assignment leaks one level up, which the first amendment missed: Claude Code
  resumes a conversation into a NEW `session_id` carrying the prior context, so
  the same logical conversation can be treated and then held with the injected
  text still in the window. Assignment must key on something stable across a
  resume, or resumed conversations must be excluded from both arms.
- **Primary outcome: unsettled, and step 2 is not authorised until it is.** The
  first draft proposed "did the session call `search_lessons` / `search_code` at
  least once", claiming the bias ran against the feature because a sufficient
  injection makes the call unnecessary. **That is backwards under the design this
  ADR actually points at.** The precedent it follows is the handles-only
  catalogue, which names `search_lessons` in its own text; an injection shaped
  the same way names the call, and naming a call mechanically produces the call.
  The outcome would then be near-tautological - it measures that a pointer
  pointed, not that a lesson helped - and it would inflate in the feature's
  favour, destroying the one antidote to an author grading his own proposal.

  Both escapes are closed: an injection that does NOT name the call is not
  actionable, and "was the retrieved lesson useful" is not automatically
  measurable. So the A/B is **demoted below the pilot** rather than specified
  here. If a non-tautological outcome exists it will come from the pilot's
  precision sample, and it gets written into an amendment before any injection
  ships.

- **Baseline drift, unnamed in the first draft.** The 1.2% was measured hours
  BEFORE the SessionStart catalogue landed - and that catalogue names
  `search_lessons` in every session it renders. Step 2 would run entirely in the
  post-catalogue world, where the catalogue alone could move the rate. Any
  comparison must be against a concurrently-held arm, never against the 1.2%
  historical figure, and the baseline metric must be the same metric as the
  outcome (the first draft baselined on `search_lessons` alone and proposed an
  outcome of `search_lessons` OR `search_code`).
- **Three verdicts, not two.** WIN: interval excludes the baseline, upward.
  LOSE: interval excludes it downward, or latency exceeds budget. INCONCLUSIVE:
  anything else. Given the power table below, INCONCLUSIVE is the most likely
  outcome and must be named in advance so it cannot be reinterpreted later.

**Power.** Human prompts, last 30 days, main sessions: 1668 across 24 active
dates, median 58 per active day. Sessions are the unit: 409 main sessions in 30
days. Eligible sessions are unknown until the pilot runs. Against the measured
1.2% baseline (which itself drifts - see above):

| n per arm | minimum detectable rate at 80% power |
|---|---|
| 95 | 12.1% |
| 190 | 8.0% |
| 380 | 5.7% |

**This can only see a large effect.** 1.2% -> 4% is invisible at any reachable n
(power 0.15-0.44). Acceptable: an effect too small to detect is too small to
justify tokens on every prompt.

**Stopping rule:** n-based only - 190 sessions per arm - with a 90-day calendar
cap after which the result is INCONCLUSIVE by definition. The first draft's "190
per arm or 30 days, whichever comes first" would have terminated at ~28 per arm
if the match rate is low, and called that a result.

### The stopping rule contradicts constraint 2, and the pilot must resolve it

Main sessions run at 409 per 30 days = 13.6/day, so the 90-day cap yields ~1227
sessions. Reaching 380 eligible ones (190 per arm) therefore needs an
eligibility rate of **31% or higher**:

| eligibility | eligible in 90 days | reaches 380? |
|---|---|---|
| 100% | 1227 | yes |
| 50% | 613 | yes |
| 31% | 380 | exactly |
| 20% | 245 | no |
| 10% | 123 | no |

Constraint 2 requires the opposite: silence must be the common case. **A design
that fires on 31% of every message the user types is the wallpaper that
constraint exists to prevent.** As written, this experiment cannot both respect
its own constraint and reach its own n.

That is not resolved by argument, and it is why the pilot is the only authorised
step. When the eligibility rate is measured, exactly one of these is chosen -
explicitly, in an amendment to this ADR, before any injection ships:

- **eligibility >= 31%:** the threshold is too loose for constraint 2. Tighten
  it and re-pilot; do not run the experiment at that rate just because it is
  powered.
- **eligibility 10-31%:** the A/B is runnable only over 5-11 months. Either
  accept that horizon or accept a permanently INCONCLUSIVE verdict and decide on
  the descriptive pilot data alone, stating that no causal claim is available.
- **eligibility < 10%:** the matcher fires too rarely to be worth a hook on the
  hottest path. Retire the route.

## Limitations

- **Builder, designer and reader-out are the same agent.** No independent party
  sets the threshold or interprets the interval. The first draft claimed the
  primary outcome's bias mitigated this; it does not, because that bias runs the
  other way. What remains is weaker: fixed verdicts, an n-based stopping rule,
  and one external review per revision of this document.
- **The traffic that motivates this feature is the traffic the feature's own
  development generates.** The sessions in the numerator are dogfood and
  benchmark runs of AXON and FORGE, not organic work in an unrelated repo. n = 1
  machine, one user, one model family, and one self-referential workload.
- **The mechanism hypothesis rests on one confounded comparison.**
- **The Read hook's effect remains unmeasured.** Seven days of post-fix data
  cannot settle it, and a future reader should not cite this ADR as evidence
  that it failed.

## Consequences

- A fourth delivery channel on the hottest path in the loop.
- If the pilot shows a low match rate, the honest move is to stop - not to lower
  the threshold until enough prompts match, which manufactures eligibility.
- If step 2 returns null, the conclusion is not "try a fifth channel": it is that
  in-conversation push does not work here, and the remaining lever is naming the
  call in the task brief - FORGE's territory, not a hook.

## What is deliberately not decided here

The matcher (embedding versus trigger keywords), the threshold value, and the
injection wording. They are only meaningful once the pilot's score distribution
exists; fixing them now is guessing.

## Change log

- **2026-09-02 (fourth pass)** - the premise itself was wrong. "32 lessons, 0
  delivered" was read as a measurement; the counters were created 2026-09-01
  with default 0, so it is absence of past measurement. Independent evidence
  contradicts the reading: 49 `search_lessons` calls in the 30 days to
  2026-09-01, against a corpus 11 days old. The delivery bottleneck this whole
  document assumes is unestablished, so a Step 0 was added ahead of the pilot:
  a retrospective replay measuring whether the corpus answers real questions,
  which gates every downstream item and needs no new mechanism.
- **2026-09-01 (third pass, external)** - reviewed by GLM-5.3, which re-derived
  every figure from the raw transcripts and found that the second pass had fixed
  the wording without re-verifying the data. Five further holes: the asserted
  mechanism ("FORGE's briefs name the call") is FALSE - the caller is the FORGE
  orchestrator running its own process document (`task.md:273`, `anneal.md:35`),
  which is mandatory workflow compliance and not the incidental reminder this
  ADR proposes, so the motivating observation measures a different mechanism;
  the primary outcome's bias runs FOR the feature, not against it, because a
  pointer that names `search_lessons` mechanically produces the call (outcome
  now unsettled and the A/B demoted below the pilot); the 1.2% baseline was
  measured hours before the SessionStart catalogue that names the same call, so
  it cannot serve as a comparison baseline; "1890 prompts across 38 active days"
  is impossible in a 30-day window (the same mtime-versus-event bug as the Read
  hook table - corrected to 1668 across 24 dates); per-`session_id`
  randomisation still leaks, because Claude Code resumes a conversation into a
  new id carrying its context. Also: the hit population is the tool's own
  dogfood and benchmark traffic, not organic use; and no analysis script was
  committed, so no figure was reproducible - `scripts/analysis/delivery_baseline.py`
  now derives them all and every count in this document moved when the window
  filter was fixed (main 6/337 -> 5/409, parents 19 -> 20, reachability 37.6% ->
  31%).
- **2026-09-01 (second pass)** - two further holes found while the document was
  still open: the surviving 1.8%-vs-7.9% evidence is CLUSTERED (12 of 16 hits in
  one parent session), so p = 0.0005 was inflated - recomputed on 19 independent
  parents, where the direction survives but the interval is four times wider;
  and the stopping rule needs 37.6% eligibility to reach n inside the 90-day
  cap, which directly contradicts constraint 2 - now written as a three-way
  decision the pilot data must force.
- **2026-09-01** - amended before sealing, after a negative-flow review of the
  first draft found: the central measurement wrong three ways and pointing the
  other way once corrected (now retracted to "unmeasured"); a primary outcome
  that could penalise success (retained, weakness stated); per-prompt
  randomisation that leaks within a session (changed to per-session); no
  INCONCLUSIVE verdict (added); a stopping rule that could terminate at ~28 per
  arm (changed to n-based with a calendar cap); "the only such point" false
  against `SubagentStart` (corrected, with the real reason for the choice); an
  unbased 10-20% eligibility assumption (replaced by a shadow pilot, now the
  only authorised step); no latency budget (added, 150 ms p95); decision count
  869 -> 876 (counted).
