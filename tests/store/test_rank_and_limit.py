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
    from axon.store.vector_common import _rank_and_limit
    results = [_r(i, "word " * 10) for i in range(10)]
    out = _rank_and_limit(
        results, top_k=3, max_nodes=25, max_tokens=10_000,
        now=datetime(2025, 1, 2, tzinfo=UTC),
    )
    assert len(out) == 3


def test_rank_and_limit_respects_token_budget() -> None:
    from axon.store.vector_common import _rank_and_limit
    # each content ~ 400 chars -> ~100 estimated tokens; budget 150 fits 1
    results = [_r(i, "x" * 400) for i in range(5)]
    out = _rank_and_limit(
        results, top_k=5, max_nodes=25, max_tokens=150,
        now=datetime(2025, 1, 2, tzinfo=UTC),
    )
    assert len(out) == 1


def test_rank_and_limit_keeps_top_hit_when_it_alone_exceeds_budget() -> None:
    """A valid query with one plausible hit must never return empty just because
    that top hit's content alone is larger than the token budget (regression for
    the drop-everything bug in EMB-4)."""
    from axon.store.vector_common import _rank_and_limit
    # content ~400 chars -> ~100 estimated tokens; budget 10 is smaller than any hit
    results = [_r(0, "x" * 400)]
    out = _rank_and_limit(
        results, top_k=5, max_nodes=25, max_tokens=10,
        now=datetime(2025, 1, 2, tzinfo=UTC),
    )
    assert len(out) == 1


def test_trim_to_budget_preserves_input_order_and_limits_tail() -> None:
    from axon.store.vector_common import _trim_to_budget

    results = [
        {"id": "b", "payload": {"content": "b" * 200}},
        {"id": "a", "payload": {"content": "a" * 200}},
        {"id": "c", "payload": {"content": "c" * 400}},
    ]

    out = _trim_to_budget(results, max_nodes=2, max_tokens=150)

    assert [item["id"] for item in out] == ["b", "a"]


def test_trim_to_budget_keeps_top_hit_when_it_alone_exceeds_budget() -> None:
    from axon.store.vector_common import _trim_to_budget

    results = [{"id": "b", "payload": {"content": "b" * 400}}]

    out = _trim_to_budget(results, max_nodes=2, max_tokens=10)

    assert [item["id"] for item in out] == ["b"]


def test_rank_and_limit_prefers_requested_ctx_when_scores_are_close() -> None:
    """dec-131: a non-protected ctx orders retrieval instead of partitioning it.

    Measured 2026-08-29: real scores spread only 0.05 across 20 hits, so a hit
    from the requested ctx must outrank a marginally better hit from another
    collection - and the other collection must still be reachable.
    """
    from axon.store.vector_common import _rank_and_limit

    other = {**_r(0, "other"), "payload": {**_r(0, "other")["payload"], "ctx": "personal"}}
    wanted = {**_r(1, "wanted"), "payload": {**_r(1, "wanted")["payload"], "ctx": "knowledge"}}
    out = _rank_and_limit(
        [other, wanted], top_k=2, max_nodes=25, max_tokens=10_000,
        now=datetime(2025, 1, 2, tzinfo=UTC), prefer_ctx="knowledge",
    )
    assert [hit["id"] for hit in out] == ["1", "0"]


def test_rank_and_limit_ctx_preference_does_not_override_a_clearly_better_hit() -> None:
    from axon.store.vector_common import _rank_and_limit

    strong = {**_r(0, "strong"), "payload": {**_r(0, "strong")["payload"], "ctx": "personal"}}
    weak = {**_r(30, "weak"), "payload": {**_r(30, "weak")["payload"], "ctx": "knowledge"}}
    out = _rank_and_limit(
        [strong, weak], top_k=2, max_nodes=25, max_tokens=10_000,
        now=datetime(2025, 1, 2, tzinfo=UTC), prefer_ctx="knowledge",
    )
    assert [hit["id"] for hit in out] == ["0", "30"]
