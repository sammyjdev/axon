# dec-131: ctx partitions retrieval, and that is why compression looked broken

- Status: Accepted (decision 1 implemented; 2 and 3 not yet enacted)
- Date: 2026-08-29
- Relates to: dec-118 (rtkx compression tier), dec-119 (canonical activity
  stores), dec-109 (restricted-context isolation), issue #168

## Context

Issue #168 reported that every compression event had recorded 0.0% since
2026-06-26 and that the published p50 of 85.5% described a closed window. The
investigation started there and ended somewhere else: **compression was never
the defect.** It was the symptom of a retrieval that returns almost nothing.

Everything below is measured, on this machine, on 2026-08-29.

### The 121 empty packs

Every compression event in August has `before_tokens` of **exactly 26** - not a
median, the same value 121 times - with `engine=disabled` and, once
`rejection_note` existed, `strategy=minimal`. All 121 carry `caller=claude-code`
and `ctx=knowledge`, spread across working days in proportion to actual use.

A pack of 26 tokens is a header with nothing under it. The strategy falls to
`minimal` because there is nothing to compress, compression is skipped by
design, and the row lands as `0.0%` - indistinguishable from a compressor that
ran and gained nothing.

### Why the packs were empty

Content is partitioned by ctx, and the partition splits code from decisions:

| query | `ctx=knowledge` | `ctx=personal` |
|---|---|---|
| `repo_identity git-common-dir` | **0 hits** | 5 hits |
| `dec-129` | 5 hits | **0 hits** |

`index-dev` writes repository code under `ctx=personal`; the vault and decisions
land under `ctx=knowledge`. `get_search_collections` then returns `[ctx]` for any
registered context, so **a call with `ctx=knowledge` can never see code, and a
call with `ctx=personal` can never see a decision.**

That is not what the barrier was built for. `collections.py` says so in its own
docstring - "work só é acessível com ctx='work' explícito — protege IP da
Avangrid" - and only `work` is in `PROTECTED_CONTEXTS`; the other four are
`restricted: False`. `DEFAULT_SEARCH_CONTEXTS` is `(personal, career, knowledge,
saas)`, which is the intended behaviour and is used only when no ctx is given.
Supplying a non-protected ctx restricts exactly as hard as supplying `work`.

### The retrieval does not discriminate either

With the partition in place, scores across 20 hits are flat:

    0.53 0.51 0.50 0.50 0.50 0.50 0.50 0.50 0.50 0.50 0.50 0.49 ... 0.48

The 8th scores 0.498; the mean of ranks 9-16 is 0.494. **A 0.8% drop.** Cutting
at 8 segments is not selection, it is a coin toss - and it is what a similarity
search returns when the material that would answer the query is in the partition
it cannot see.

### Compression, measured, on the material that does circulate

Four compressors over a real pack (14,784 chars, 4 required symbols, 328 terms):

| compressor | rate | symbols lost | vocabulary lost | time |
|---|---|---|---|---|
| llama-3.1-8b (current) | 18.6% | 1/4 | 24/328 | 123s |
| Qwen3.5-9B | 100% | 4/4 | 328/328 (empty output) | 103s |
| phi-4 | 0.1% | 1/4 | 5/328 | 51s |
| Mistral-Small-24B | 14.3% | **0/4** | **2/328** | 92s |
| gemma-3-27b | 55.6% | 1/4 | 185/328 | 70s |
| Llama-3.3-70B | timeout | - | - | >180s |

The relationship is consistent: whatever compresses much destroys content, and
whatever preserves content compresses almost nothing. On larger blocks the
current model does not merely fail - it **expands**: 14,784 chars became 63,553
after 8.5 minutes, which the removal of `max(0, before - after)` finally made
visible.

The reason is structural, not a matter of model choice. Retrieval already
selects 8 segments and truncates at 8,000 chars, so what reaches the compressor
is a digest of a digest; squeezing it again can only remove information.

### rtk is being used outside its domain

`rtk read` is called with no `--level`, whose default is `none` - "full content".
That alone explains the 0.0% it contributes. But `--level aggressive` is not the
fix either, because it behaves completely differently depending on the material:

| input | rate | outcome |
|---|---|---|
| single code file | 91.3% | keeps imports and every signature with types; **drops module constants** (`SERIES`, `_TS_KEYS`) |
| assembled context pack | 97.2% | destroys it - 325 of 328 terms gone |
| command output (`git log --stat`) | 0.9% | nothing to filter |

A concatenated pack has no structure left for a structural filter to reason
about, so it truncates instead. And even on a single file, what it drops is the
declarative part - which in this codebase is exactly where decisions live
(`SERIES`, `EXCLUDED_DIR_NAMES`, `DEFAULT_RETRIEVAL_STRATEGIES`).

## Decision

**1. A non-protected ctx orders retrieval; it does not partition it.**
`get_search_collections` returns `[ctx]` only for `PROTECTED_CONTEXTS`. For the
other four, search all non-protected collections and let the requested ctx
influence ranking. `work` is untouched: still isolated, still requiring an
explicit request. This restores what `DEFAULT_SEARCH_CONTEXTS` already
describes.

**2. Semantic compression is disabled in the recall path.** No available model
gives a usable trade-off, and the cost is 50s to 8.5min per call. It stays in
the codebase, with its guard, for whenever the input is genuinely large - which
today it never is.

**3. rtk keeps its terminal-command role and stays out of pack assembly** until
a filter exists that preserves declarative blocks. `--level` should be explicit
wherever rtk is invoked, so no caller silently gets "full content" while
believing it compresses.

## Consequences

- A question about code asked with `ctx=knowledge` starts finding code. Measured
  after the change: `repo_identity git-common-dir` under `ctx=knowledge` went
  from 0 hits / a 26-token pack to 8 hits / 6,484 chars. The 121 empty packs
  would have come back full.
- The flat score curve was an artifact of the partition. On five queries, four
  could not even produce 16 hits inside `knowledge` alone; the one that could
  went from a 6.1% to a 9.3% drop between rank 1 and the mean of ranks 9-16,
  with the top score rising 0.526 -> 0.553. Across all five the drop is now
  6.1%-17.8%. It discriminates, but not enough to call a cut at 8 principled -
  on `chunker java estrutura fixture` the mean of ranks 9-16 (0.547) still
  edges out rank 8 (0.542).
- Recall gets slower per call (four collections instead of one) and returns more
  candidates. The flat score curve should separate once the material that
  answers the query can actually be reached - and if it does not, the next
  suspect is the embedding of the query itself, not the partition.
- `work` isolation is unchanged. Any future change here must keep
  `PROTECTED_CONTEXTS` as a hard boundary; this decision narrows the barrier to
  what its own docstring claims, nothing further.
- The compression figures in `docs/METRICS.md` stay marked as a June window.
  They cannot be recomputed into anything meaningful until retrieval returns
  real packs.
