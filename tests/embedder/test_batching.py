from __future__ import annotations

import pytest

from axon.embedder.chunker import Chunk


def _make_chunk(content: str, symbol: str = "f") -> Chunk:
    return Chunk(
        symbol=symbol,
        chunk_type="function",
        start_line=1,
        end_line=content.count("\n") + 1,
        content=content,
        file_path="test.py",
        language="python",
    )


def test_estimate_tokens_overestimates() -> None:
    """_estimate_tokens must use 0.35 chars/token (overestimate for safety)."""
    from axon.embedder.pipeline import _estimate_tokens

    text = "x" * 100
    result = _estimate_tokens(text)
    # 100 * 0.35 = 35; must be >= 35 (overestimate), not 25 (0.25 would be underestimate)
    assert result >= 35, f"Expected >= 35, got {result}"
    # Must return at least 1 even for empty string
    assert _estimate_tokens("") == 1


def test_estimate_tokens_never_zero() -> None:
    from axon.embedder.pipeline import _estimate_tokens

    assert _estimate_tokens("") == 1
    assert _estimate_tokens("a") >= 1


def test_make_token_bounded_batches_no_overflow() -> None:
    """No batch must exceed _MAX_BATCH_TOKENS in estimated tokens."""
    from axon.embedder.pipeline import (
        _MAX_BATCH_TOKENS,
        _estimate_tokens,
        _make_token_bounded_batches,
    )

    # 10 chunks each with 300 chars (~105 estimated tokens)
    chunks = [_make_chunk("x" * 300, f"f{i}") for i in range(10)]
    batches = _make_token_bounded_batches(chunks)

    for batch in batches:
        batch_tokens = sum(_estimate_tokens(c.content) for c in batch)
        # A batch may exceed _MAX_BATCH_TOKENS only if it contains a single
        # giant chunk that exceeds the limit on its own.
        if len(batch) > 1:
            assert batch_tokens <= _MAX_BATCH_TOKENS, (
                f"Batch of {len(batch)} chunks has {batch_tokens} tokens > {_MAX_BATCH_TOKENS}"
            )


def test_make_token_bounded_batches_preserves_all_chunks() -> None:
    """All chunks must appear in exactly one batch (no chunk dropped or duplicated)."""
    from axon.embedder.pipeline import _make_token_bounded_batches

    chunks = [_make_chunk("word " * 50, f"f{i}") for i in range(20)]
    batches = _make_token_bounded_batches(chunks)

    flattened = [c for batch in batches for c in batch]
    assert len(flattened) == len(chunks), f"Expected {len(chunks)} chunks, got {len(flattened)}"
    # order preserved, not just membership
    assert [c.symbol for c in flattened] == [c.symbol for c in chunks]


def test_make_token_bounded_batches_giant_chunk_own_batch() -> None:
    """A single chunk exceeding _MAX_BATCH_TOKENS goes in its own batch (not dropped)."""
    from axon.embedder.pipeline import _MAX_BATCH_TOKENS, _make_token_bounded_batches

    # Create a chunk that definitely exceeds the budget at 0.35 chars/token:
    # _MAX_BATCH_TOKENS / 0.35 chars_per_token -> chars needed to exceed budget
    chars_needed = int(_MAX_BATCH_TOKENS / 0.35) + 100
    giant = _make_chunk("x " * (chars_needed // 2), "giant_func")
    normal = _make_chunk("short", "normal_func")

    batches = _make_token_bounded_batches([giant, normal])
    # giant must be alone in its batch
    giant_batches = [b for b in batches if any(c.symbol == "giant_func" for c in b)]
    assert len(giant_batches) == 1
    assert len(giant_batches[0]) == 1, "Giant chunk must be in its own batch"


def test_make_token_bounded_batches_giant_in_middle() -> None:
    """A giant chunk sandwiched between normal chunks: nothing dropped, order
    preserved, giant alone in its own batch (the midstream flush path)."""
    from axon.embedder.pipeline import _MAX_BATCH_TOKENS, _make_token_bounded_batches

    chars_needed = int(_MAX_BATCH_TOKENS / 0.35) + 100
    n1 = _make_chunk("short a", "n1")
    giant = _make_chunk("x " * (chars_needed // 2), "giant")
    n2 = _make_chunk("short b", "n2")

    batches = _make_token_bounded_batches([n1, giant, n2])
    flat = [c for b in batches for c in b]
    # no drop + order preserved across the midstream giant
    assert [c.symbol for c in flat] == ["n1", "giant", "n2"]
    giant_batch = next(b for b in batches if any(c.symbol == "giant" for c in b))
    assert len(giant_batch) == 1, "midstream giant must be alone in its batch"


def test_make_token_bounded_batches_empty_input() -> None:
    from axon.embedder.pipeline import _make_token_bounded_batches

    assert _make_token_bounded_batches([]) == []


def test_max_batch_tokens_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """AXON_MAX_BATCH_TOKENS env var must override the default."""
    import importlib

    import axon.embedder.pipeline as pipeline_mod

    monkeypatch.setenv("AXON_MAX_BATCH_TOKENS", "1024")
    try:
        importlib.reload(pipeline_mod)
        assert pipeline_mod._MAX_BATCH_TOKENS == 1024
    finally:
        # delenv BEFORE the restore reload, otherwise the module reloads with
        # the override still set and leaks 1024 into every later test.
        monkeypatch.delenv("AXON_MAX_BATCH_TOKENS", raising=False)
        importlib.reload(pipeline_mod)
    assert pipeline_mod._MAX_BATCH_TOKENS == 8192
