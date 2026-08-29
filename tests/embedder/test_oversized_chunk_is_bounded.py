"""A single chunk must not exceed what the provider accepts.

`_make_token_bounded_batches` caps the BATCH at _MAX_BATCH_TOKENS and documents
that a chunk over budget "is placed in its own batch (never dropped)". Never
dropping it is right; sending it whole is not. On 2026-08-29 the revvo project
failed to index at all because `worker-configuration.d.ts` - 512,525 characters
of wrangler-generated types - was handed to DeepInfra in one request, which
answered 422 and aborted the project.

Truncation keeps the intent: the chunk is still indexed, by its head, which is
where a generated file's meaningful content is. Dropping it would lose the file
entirely; sending it whole loses the entire project.
"""

from __future__ import annotations

from axon.embedder.pipeline import _MAX_BATCH_TOKENS, _make_token_bounded_batches


class _Chunk:
    def __init__(self, content: str) -> None:
        self.content = content


def test_a_chunk_larger_than_the_budget_is_truncated_not_sent_whole() -> None:
    oversized = _Chunk("palavra " * 200_000)  # ~1.6M chars, far past any limit

    batches = _make_token_bounded_batches([oversized])

    sent = [c.content for b in batches for c in b]
    assert len(sent) == 1, "the chunk must survive - dropping it loses the file"
    assert len(sent[0]) < len(oversized.content), (
        "an oversized chunk was passed through whole; the provider rejects it "
        "with 422 and the whole project fails to index"
    )


def test_truncation_respects_the_token_budget() -> None:
    oversized = _Chunk("x " * 100_000)

    batches = _make_token_bounded_batches([oversized])
    content = batches[0][0].content

    # 4 chars/token is the estimator this module uses.
    assert len(content) / 4 <= _MAX_BATCH_TOKENS


def test_normal_chunks_are_untouched() -> None:
    chunks = [_Chunk("def f(): pass"), _Chunk("class A: ...")]

    batches = _make_token_bounded_batches(chunks)

    assert [c.content for b in batches for c in b] == [c.content for c in chunks]
