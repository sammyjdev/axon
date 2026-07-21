"""complete_with_usage() must fail over to a fallback model instead of
silently degrading to usage_source=estimate on any completion-provider error.

Root cause (see issue #100): the completions path had no equivalent of the
embeddings path's `embed_via_chain` fall-through, and the caller in app.py
swallows the exception with no logging. These tests pin:

- a normal success path is unaffected (single call, no warning log),
- a tier with a distinct fallback model retries once and returns the
  fallback's real usage when the primary fails,
- when the fallback also fails, the ORIGINAL (primary) exception propagates,
- a tier whose fallback helper returns the SAME model as the primary
  (TRIVIAL_COMPLETION/UNKNOWN, already bottom tier) never gets a blind
  identical-model retry,
- every failure path logs at WARNING level or above with the model name(s)
  and the exception message.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from axon.router.classifier import TaskType
from axon.router.engine import (
    CompletionUsage,
    TaskRequest,
    complete_with_usage,
)


class _FakeBreaker:
    """Records every allow_call/record_success/record_failure by key."""

    def __init__(self, deny_keys: set[str] | None = None) -> None:
        self.allow_calls: list[str] = []
        self.successes: list[str] = []
        self.failures: list[str] = []
        self._deny_keys = deny_keys or set()

    def allow_call(self, key: str) -> bool:
        self.allow_calls.append(key)
        return key not in self._deny_keys

    def record_success(self, key: str) -> None:
        self.successes.append(key)

    def record_failure(self, key: str) -> None:
        self.failures.append(key)


def _ok_response(content: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _patch_pipeline(
    monkeypatch,
    task_type: TaskType,
    fake_acompletion,
    deny_keys: set[str] | None = None,
) -> _FakeBreaker:
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (task_type, "local"),
    )
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 0.0)
    monkeypatch.setattr("axon.router.engine.provider_for_model", lambda _m: "anthropic")
    monkeypatch.setattr(
        "axon.router.engine.validate_anthropic_cache_control", lambda _msgs: None
    )
    monkeypatch.setattr(
        "axon.router.engine.count_tokens_for_provider", lambda _p, _m: 100
    )
    breaker = _FakeBreaker(deny_keys=deny_keys)
    monkeypatch.setattr("axon.router.engine._BREAKER", breaker)
    monkeypatch.setattr("axon.router.engine.litellm.acompletion", fake_acompletion)
    return breaker


@pytest.mark.asyncio
async def test_primary_success_is_unaffected(monkeypatch, caplog) -> None:
    calls: list[str] = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs["model"])
        return _ok_response()

    breaker = _patch_pipeline(monkeypatch, TaskType.CODE_ANALYSIS, fake_acompletion)

    with caplog.at_level(logging.WARNING, logger="axon.router.engine"):
        content, usage = await complete_with_usage(
            TaskRequest(content="q", ctx="knowledge"), messages=[]
        )

    assert content == "ok"
    assert isinstance(usage, CompletionUsage)
    assert len(calls) == 1
    assert breaker.successes == [f"router:{calls[0]}"]
    assert breaker.failures == []
    assert caplog.records == []


@pytest.mark.asyncio
async def test_architecture_tier_falls_over_to_mid_tier(monkeypatch, caplog) -> None:
    from axon.router.engine import _mid_tier_model, _top_tier_model

    primary_model = _top_tier_model()
    fallback_model = _mid_tier_model()
    assert primary_model != fallback_model

    calls: list[str] = []

    async def fake_acompletion(**kwargs):
        model = kwargs["model"]
        calls.append(model)
        if model == primary_model:
            raise ConnectionError("NIM ConnectError: connection refused")
        return _ok_response(content="fallback answer")

    breaker = _patch_pipeline(monkeypatch, TaskType.ARCHITECTURE, fake_acompletion)

    with caplog.at_level(logging.WARNING, logger="axon.router.engine"):
        content, usage = await complete_with_usage(
            TaskRequest(content="design the system", ctx="knowledge"), messages=[]
        )

    assert calls == [primary_model, fallback_model]
    assert content == "fallback answer"
    assert isinstance(usage, CompletionUsage)
    assert usage.model == fallback_model
    assert breaker.failures == [f"router:{primary_model}"]
    assert breaker.successes == [f"router:{fallback_model}"]

    warning_text = " ".join(r.getMessage() for r in caplog.records)
    assert primary_model in warning_text
    assert "NIM ConnectError" in warning_text

    # The "recovered via fallback" confirmation must be its OWN record at
    # WARNING+ (caplog is scoped to WARNING+, so a downgrade to DEBUG would
    # drop it from caplog.records entirely) -- distinct from the earlier
    # "completion failed for model=..." record, which alone must not satisfy
    # this pin.
    recovery_records = [
        r for r in caplog.records if "recovered" in r.getMessage().lower()
    ]
    assert recovery_records, "expected a distinct 'recovered via fallback' log record"
    assert recovery_records[0].levelno >= logging.WARNING
    assert fallback_model in recovery_records[0].getMessage()
    assert len(caplog.records) >= 2


@pytest.mark.asyncio
async def test_deep_reasoning_tier_also_falls_over_to_mid_tier(monkeypatch) -> None:
    from axon.router.engine import _mid_tier_model, _top_tier_model

    primary_model = _top_tier_model()
    fallback_model = _mid_tier_model()

    async def fake_acompletion(**kwargs):
        if kwargs["model"] == primary_model:
            raise RuntimeError("500 from provider")
        return _ok_response(content="deep fallback")

    _patch_pipeline(monkeypatch, TaskType.DEEP_REASONING, fake_acompletion)

    content, usage = await complete_with_usage(
        TaskRequest(content="reason deeply", ctx="knowledge"), messages=[]
    )

    assert content == "deep fallback"
    assert usage is not None
    assert usage.model == fallback_model


@pytest.mark.asyncio
async def test_code_analysis_fallback_also_fails_propagates_original(
    monkeypatch, caplog
) -> None:
    from axon.router.engine import _bottom_tier_model, _mid_tier_model

    primary_model = _mid_tier_model()
    fallback_model = _bottom_tier_model()
    assert primary_model != fallback_model

    calls: list[str] = []

    async def fake_acompletion(**kwargs):
        model = kwargs["model"]
        calls.append(model)
        if model == primary_model:
            raise ConnectionError("primary connect error")
        raise RuntimeError("fallback also down")

    breaker = _patch_pipeline(monkeypatch, TaskType.CODE_ANALYSIS, fake_acompletion)

    with caplog.at_level(logging.WARNING, logger="axon.router.engine"):
        with pytest.raises(ConnectionError, match="primary connect error"):
            await complete_with_usage(
                TaskRequest(content="analyze this diff", ctx="knowledge"), messages=[]
            )

    assert calls == [primary_model, fallback_model]
    assert breaker.failures == [f"router:{primary_model}", f"router:{fallback_model}"]
    assert breaker.successes == []

    warning_text = " ".join(r.getMessage() for r in caplog.records)
    assert primary_model in warning_text
    assert fallback_model in warning_text
    assert "primary connect error" in warning_text


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", [TaskType.TRIVIAL_COMPLETION, TaskType.UNKNOWN])
async def test_bottom_tier_has_no_distinct_fallback_no_blind_retry(
    monkeypatch, caplog, task_type
) -> None:
    calls: list[str] = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs["model"])
        raise ConnectionError("bottom tier down")

    breaker = _patch_pipeline(monkeypatch, task_type, fake_acompletion)

    with caplog.at_level(logging.WARNING, logger="axon.router.engine"):
        with pytest.raises(ConnectionError, match="bottom tier down"):
            await complete_with_usage(
                TaskRequest(content="q", ctx="knowledge"), messages=[]
            )

    # Exactly one call -- no blind retry against the identical model.
    assert len(calls) == 1
    assert breaker.failures == [f"router:{calls[0]}"]
    assert breaker.successes == []
    assert len(caplog.records) >= 1
    assert "bottom tier down" in caplog.records[0].getMessage()


@pytest.mark.asyncio
async def test_fallback_denied_by_breaker_never_calls_fallback_model(
    monkeypatch, caplog
) -> None:
    """When the fallback model's own breaker is open, the fallback must not
    be attempted at all (no doomed extra round-trip) -- the ORIGINAL primary
    exception propagates, and no failure/success is recorded for a fallback
    call that was never made.
    """
    from axon.router.engine import _mid_tier_model, _top_tier_model

    primary_model = _top_tier_model()
    fallback_model = _mid_tier_model()
    assert primary_model != fallback_model

    calls: list[str] = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs["model"])
        raise ConnectionError("primary connect error")

    breaker = _patch_pipeline(
        monkeypatch,
        TaskType.ARCHITECTURE,
        fake_acompletion,
        deny_keys={f"router:{fallback_model}"},
    )

    with caplog.at_level(logging.WARNING, logger="axon.router.engine"):
        with pytest.raises(ConnectionError, match="primary connect error"):
            await complete_with_usage(
                TaskRequest(content="design the system", ctx="knowledge"), messages=[]
            )

    # Only the primary was ever called -- the fallback's breaker being open
    # must short-circuit before _call_completion is reached.
    assert calls == [primary_model]
    assert breaker.failures == [f"router:{primary_model}"]
    assert breaker.successes == []
    assert f"router:{fallback_model}" in breaker.allow_calls

    warning_text = " ".join(r.getMessage() for r in caplog.records)
    assert fallback_model in warning_text
    assert any(r.levelno >= logging.WARNING for r in caplog.records)
