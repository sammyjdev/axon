"""Tests for the TypeSafe benchmark client."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from axon.benchmark.typesafe import client
from axon.benchmark.typesafe.questions import PRICE_PER_MTOK_INPUT


def _response(
    answers: dict,
    *,
    input_tokens: int = 12,
    output_tokens: int = 3,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "answers": answers,
            "model": "jev-1.13.0",
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    )


def _client(
    tmp_path,
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs,
) -> client.TypeSafeClient:
    return client.TypeSafeClient(
        cache=client.JsonCache(tmp_path / "typesafe-cache.json"),
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_cache_key_ignores_dict_key_order() -> None:
    first = client.cache_key(
        state={"summary": "decision", "scope": ["a.py"]},
        question_map={"judge": {"type": "score", "criteria": ["no", "yes"]}},
        model="jev-1.13.0",
        sample_index=0,
    )
    second = client.cache_key(
        state={"scope": ["a.py"], "summary": "decision"},
        question_map={"judge": {"criteria": ["no", "yes"], "type": "score"}},
        model="jev-1.13.0",
        sample_index=0,
    )
    assert first == second


def test_cache_key_includes_sample_index() -> None:
    base = dict(state={"summary": "decision"}, question_map={"q": {}}, model="jev-1.13.0")
    assert client.cache_key(**base, sample_index=0) != client.cache_key(**base, sample_index=1)


@pytest.mark.parametrize(
    "answers",
    [
        {"supersession": {"type": "noul", "noul": 0.95}},
        {
            "choice": {
                "type": "choice",
                "choice": "true",
                "probabilities": {"true": 0.9, "false": 0.1},
                "confidence": 0.9,
            }
        },
        {
            "score": {
                "type": "score",
                "score": 4,
                "legend": ["poor", "good"],
                "probabilities": [0.1, 0.9],
                "confidence": 0.9,
            }
        },
    ],
)
async def test_ask_preserves_typesafe_answer_payload(tmp_path, answers: dict) -> None:
    tested_client = _client(tmp_path, lambda request: _response(answers))

    answer = await tested_client.ask({"decision": "x"}, {"question": {"type": "noul"}})

    assert answer.payload == answers
    assert answer.model == "jev-1.13.0"
    assert answer.input_tokens == 12
    assert answer.output_tokens == 3
    assert answer.cached is False


async def test_identical_ask_uses_durable_cache(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response({"q": {"type": "noul", "noul": 0.95}})

    tested_client = _client(tmp_path, handler)
    await tested_client.ask({"decision": "x"}, {"question": {"type": "noul"}})
    cached = await tested_client.ask({"decision": "x"}, {"question": {"type": "noul"}})

    assert calls == 1
    assert cached.cached is True
    assert cached.latency_s == 0.0
    assert cached.cost_usd == 0.0


def test_json_cache_survives_reopen(tmp_path) -> None:
    path = tmp_path / "typesafe-cache.json"
    client.JsonCache(path).set("key", {"answer": 42})
    assert client.JsonCache(path).get("key") == {"answer": 42}


async def test_ask_retries_429_then_succeeds(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "0.25"})
        return _response({"q": {"type": "noul", "noul": 0.95}})

    monkeypatch.setattr(client, "_sleep", fake_sleep)
    answer = await _client(tmp_path, handler).ask({}, {"q": {"type": "noul"}})

    assert answer.cached is False
    assert calls == 2
    assert sleeps == [0.25]


async def test_ask_raises_after_exhausting_529_attempts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_sleep(delay: float) -> None:
        pass

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(529)

    monkeypatch.setattr(client, "_sleep", fake_sleep)
    with pytest.raises(client.TypeSafeError):
        await _client(tmp_path, handler, max_attempts=3).ask({}, {"q": {"type": "noul"}})
    assert calls == 3


async def test_ask_does_not_retry_401(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, text="bad key")

    with pytest.raises(client.TypeSafeError, match="bad key"):
        await _client(tmp_path, handler).ask({}, {"q": {"type": "noul"}})
    assert calls == 1


async def test_cost_uses_input_tokens_only(tmp_path) -> None:
    first = await _client(
        tmp_path,
        lambda request: _response({}, input_tokens=1_000_000, output_tokens=500_000),
    ).ask({"case": 1}, {})
    second = await _client(
        tmp_path,
        lambda request: _response({}, input_tokens=1_000_000, output_tokens=1),
    ).ask({"case": 2}, {})

    assert first.cost_usd == PRICE_PER_MTOK_INPUT == 0.042
    assert second.cost_usd == first.cost_usd
