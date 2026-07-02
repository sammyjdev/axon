import pytest

from axon.retrieval.self_correct import correct_retrieval

PACK = object()


async def _retrieve_ok(q):
    return ("REFORMULATED CONTEXT", PACK, [{"score": 0.95, "payload": {"symbol": "X"}}])


async def _retrieve_still_bad(q):
    return ("STILL BAD", PACK, [{"score": 0.05, "payload": {"symbol": "X"}}])


async def _graph_hit(hits):
    return "## Dependencias\nA -> B"


async def _graph_empty(hits):
    return ""


@pytest.mark.asyncio
async def test_disabled_returns_input_untouched():
    r = await correct_retrieval(
        "q", "personal", "CTX", PACK, [{"score": 0.05}],
        retrieve_fn=_retrieve_still_bad, judge_fn=lambda q, c: False,
        reformulate_fn=lambda q: q, graph_fn=_graph_empty, enabled=False,
    )
    assert r.code_context == "CTX"
    assert r.meta["retried"] is False and r.meta["gave_up"] is False


@pytest.mark.asyncio
async def test_sufficient_first_pass_no_retry():
    r = await correct_retrieval(
        "como funciona X", "personal", "CTX", PACK, [{"score": 0.90}],
        retrieve_fn=_retrieve_still_bad, judge_fn=lambda q, c: False,
        reformulate_fn=lambda q: "SHOULD NOT BE CALLED", graph_fn=_graph_empty,
    )
    assert r.meta == {
        "verdict": "high_score", "strategy_used": None, "retried": False, "gave_up": False,
    }
    assert r.code_context == "CTX"


@pytest.mark.asyncio
async def test_reformulate_path_recovers():
    r = await correct_retrieval(
        "como funciona a compressao", "personal", "BAD", PACK, [{"score": 0.05}],
        retrieve_fn=_retrieve_ok, judge_fn=lambda q, c: False,
        reformulate_fn=lambda q: q + " detalhado", graph_fn=_graph_empty,
    )
    assert r.code_context == "REFORMULATED CONTEXT"
    assert r.meta["strategy_used"] == "reformulate"
    assert r.meta["retried"] is True and r.meta["gave_up"] is False


@pytest.mark.asyncio
async def test_structural_query_uses_graph():
    r = await correct_retrieval(
        "quem usa AuthService", "personal", "BAD", PACK,
        [{"score": 0.05, "payload": {"symbol": "AuthService"}}],
        retrieve_fn=_retrieve_ok, judge_fn=lambda q, c: False,
        reformulate_fn=lambda q: "SHOULD NOT BE CALLED", graph_fn=_graph_hit,
    )
    assert "A -> B" in r.code_context and "BAD" in r.code_context
    assert r.meta["strategy_used"] == "graph"
    assert r.meta["gave_up"] is False


@pytest.mark.asyncio
async def test_reformulate_fails_gives_up_with_header():
    r = await correct_retrieval(
        "como funciona a compressao", "personal", "BAD", PACK, [{"score": 0.05}],
        retrieve_fn=_retrieve_still_bad, judge_fn=lambda q, c: False,
        reformulate_fn=lambda q: q, graph_fn=_graph_empty,
    )
    assert r.code_context.startswith("⚠ contexto recuperado pode ser insuficiente")
    assert r.meta["gave_up"] is True and r.meta["strategy_used"] == "reformulate"


@pytest.mark.asyncio
async def test_structural_empty_graph_gives_up():
    r = await correct_retrieval(
        "quem usa AuthService", "personal", "BAD", PACK,
        [{"score": 0.05, "payload": {"symbol": "AuthService"}}],
        retrieve_fn=_retrieve_ok, judge_fn=lambda q, c: False,
        reformulate_fn=lambda q: q, graph_fn=_graph_empty,
    )
    assert r.code_context.startswith("⚠ contexto recuperado")
    assert r.meta["gave_up"] is True and r.meta["strategy_used"] == "graph"
