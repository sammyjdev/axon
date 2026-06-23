from __future__ import annotations

from datetime import UTC, datetime


def _r(i: int, content: str) -> dict:
    return {
        "score": 1.0 - i * 0.01,
        "id": str(i),
        "payload": {
            "content": content,
            "modified_at": datetime(2025, 1, 1, tzinfo=UTC).isoformat(),
        },
    }


def test_rank_and_limit_respects_top_k() -> None:
    from axon.store.vector_store import _rank_and_limit
    results = [_r(i, "word " * 10) for i in range(10)]
    out = _rank_and_limit(
        results, top_k=3, max_nodes=25, max_tokens=10_000,
        now=datetime(2025, 1, 2, tzinfo=UTC),
    )
    assert len(out) == 3


def test_rank_and_limit_respects_token_budget() -> None:
    from axon.store.vector_store import _rank_and_limit
    # each content ~ 400 chars -> ~100 estimated tokens; budget 150 fits 1
    results = [_r(i, "x" * 400) for i in range(5)]
    out = _rank_and_limit(
        results, top_k=5, max_nodes=25, max_tokens=150,
        now=datetime(2025, 1, 2, tzinfo=UTC),
    )
    assert len(out) == 1
