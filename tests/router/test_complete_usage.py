"""complete_with_usage() must surface the provider's real token usage.

The litellm response carries `usage` (prompt/completion/total). The router
previously discarded it; these tests pin the new contract: content + typed
usage out, None usage when the provider reports none, and complete() stays
a string-returning wrapper.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from axon.router.classifier import TaskType
from axon.router.engine import (
    CompletionUsage,
    TaskRequest,
    complete,
    complete_with_usage,
)


class _FakeBreaker:
    def allow_call(self, _key: str) -> bool:
        return True

    def record_success(self, _key: str) -> None:
        return None

    def record_failure(self, _key: str) -> None:
        return None


def _patch_pipeline(monkeypatch, fake_acompletion) -> None:
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 0.0)
    monkeypatch.setattr("axon.router.engine.provider_for_model", lambda _m: "anthropic")
    monkeypatch.setattr(
        "axon.router.engine.validate_anthropic_cache_control", lambda _msgs: None
    )
    monkeypatch.setattr(
        "axon.router.engine.count_tokens_for_provider", lambda _p, _m: 100
    )
    monkeypatch.setattr("axon.router.engine._BREAKER", _FakeBreaker())
    monkeypatch.setattr("axon.router.engine.litellm.acompletion", fake_acompletion)


@pytest.mark.asyncio
async def test_complete_with_usage_returns_provider_usage(monkeypatch) -> None:
    async def fake_acompletion(**_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(
                prompt_tokens=321, completion_tokens=45, total_tokens=366
            ),
        )

    _patch_pipeline(monkeypatch, fake_acompletion)

    content, usage = await complete_with_usage(
        TaskRequest(content="explain recall", ctx="knowledge"), messages=[]
    )

    assert content == "ok"
    assert isinstance(usage, CompletionUsage)
    assert usage.prompt_tokens == 321
    assert usage.completion_tokens == 45
    assert usage.total_tokens == 366
    assert usage.model  # the routed model name, never empty


@pytest.mark.asyncio
async def test_complete_with_usage_none_when_provider_omits_usage(monkeypatch) -> None:
    async def fake_acompletion(**_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

    _patch_pipeline(monkeypatch, fake_acompletion)

    content, usage = await complete_with_usage(
        TaskRequest(content="q", ctx="knowledge"), messages=[]
    )

    assert content == "ok"
    assert usage is None


@pytest.mark.asyncio
async def test_history_precedes_the_current_turn_in_composed_messages(monkeypatch) -> None:
    # Multi-turn baseline arm (gnomon ADR-0010): the provider must see the
    # conversation in natural order - system layers, prior transcript, then
    # the current question LAST. Question-before-history scrambles the
    # conversation and degrades the baseline arm's answers.
    sent: dict = {}

    async def fake_acompletion(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(
                prompt_tokens=1, completion_tokens=1, total_tokens=2
            ),
        )

    _patch_pipeline(monkeypatch, fake_acompletion)

    history = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    ]
    await complete_with_usage(
        TaskRequest(content="second question", ctx="knowledge"), messages=history
    )

    composed = sent["messages"]
    assert [m["role"] for m in composed[:2]] == ["system", "system"]
    assert composed[2:4] == history
    assert composed[-1] == {"role": "user", "content": "second question"}


@pytest.mark.asyncio
async def test_complete_still_returns_plain_string(monkeypatch) -> None:
    async def fake_acompletion(**_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(
                prompt_tokens=1, completion_tokens=1, total_tokens=2
            ),
        )

    _patch_pipeline(monkeypatch, fake_acompletion)

    response = await complete(
        TaskRequest(content="q", ctx="knowledge"), messages=[]
    )

    assert response == "ok"
