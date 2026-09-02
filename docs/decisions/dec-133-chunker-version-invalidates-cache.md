# dec-133: a chunker change invalidates what the old chunker produced

- Status: Accepted
- Date: 2026-09-02
- Relates to: dec-132 (class header chunk), D5 (chunker quality is a release gate)

## Context

`PostgresFileCache` keys on the file's sha1: a file whose content is unchanged
is skipped on reindex. That is correct for source edits and wrong for chunker
changes, because the chunker is the other input to what gets stored.

dec-132 demonstrated the cost. After it landed and was installed, a full
`axon index-dev --project axon` run reported **75 of 575 files** and produced
**78 of the 583 expected class chunks** — the other 500 files were untouched
source, so the cache skipped them and the index kept output from the old
chunker. Nothing reported a problem: the run said "concluída".

Recovery required `DELETE FROM file_index WHERE file_path LIKE ...`, by hand,
after noticing the chunk count was wrong. That is the same failure mode this
codebase has now hit repeatedly — a thing that did not run being
indistinguishable from a thing that succeeded.

## Decision

`chunker.CHUNKER_VERSION` versions the chunker's output contract, and
`file_index` carries the version that produced each row.

`get_all_sha1s(ctx, chunker_version=...)` **omits** rows written by a different
version. The pipeline therefore sees those files as uncached and reindexes them,
without needing a branch, a flag, or knowledge of why. Legacy rows carry NULL,
never match, and reindex once.

Bump `CHUNKER_VERSION` whenever a change alters what the module emits for
unchanged source. It is `v2` today: `v1` is everything before dec-132, `v2` adds
the class-declaration chunk.

## Consequences

- A chunker improvement reaches the index on the next run instead of waiting for
  each file to be edited.
- Bumping the version reindexes the whole corpus, which costs embedding calls.
  That is the intended price and the reason the constant is a deliberate bump
  rather than a hash of the module.
- Forgetting to bump it reproduces exactly the dec-132 silence. The constant's
  comment says so; nothing enforces it.

## Alternatives rejected

- **Hash the chunker module.** Automatic, and it would reindex the corpus on
  every comment or refactor that changes no output.
- **A doctor check comparing index against a fresh chunk run.** Detects drift
  instead of preventing it, and costs a full re-chunk of the corpus to answer.
  Still worth having later; it does not replace this.
