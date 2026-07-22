"""complete_with_usage() must extract real provider usage on the fallback
path even when litellm hands the usage payload back as a plain dict rather
than an attribute-bearing object.

Root cause (issue #103): litellm's own codebase is inconsistent about how it
attaches `usage` to a completion response object. The generic completion
path (`convert_to_model_response_object`, used by groq/nvidia_nim/openrouter
under the OpenAI-compatible handler) wraps it in a `litellm.types.utils.Usage`
instance, but other internal litellm paths assign the raw provider dict
directly (e.g. `litellm/litellm_core_utils/streaming_handler.py` does
`setattr(model_response_stream, "usage", chunk["usage"])` with no wrapping).
The old extraction (`getattr(raw_usage, "prompt_tokens", 0)`) silently reads
0 for every field when `raw_usage` is dict-shaped, since a dict has no such
attributes - so real usage the provider *did* return gets understated
instead of raised loudly. These tests pin:

- a fallback response whose `usage` is a plain dict with real token counts
  must be extracted with those real values, not zeros,
- a fallback response whose `usage` attribute is genuinely absent must still
  resolve to usage=None - the fix must not invent usage out of nothing (the
  existing "provider omitted it" behavior stays intact).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from axon.router.classifier import TaskType
from axon.router.engine import CompletionUsage, TaskRequest, complete_with_usage


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


def _patch_pipeline(monkeypatch, task_type: TaskType, fake_acompletion) -> _FakeBreaker:
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (task_type, "local"),
    )
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 0.0)
    monkeypatch.setattr("axon.router.engine.provider_for_model", lambda _m: "anthropic")
    monkeypatch.setattr(
        "axon.router.engine.validate_anthropic_cache_control", lambda _msgs: None
    )
    monkeypatch.setattr("axon.router.engine.count_tokens_for_provider", lambda _p, _m: 100)
    breaker = _FakeBreaker()
    monkeypatch.setattr("axon.router.engine._BREAKER", breaker)
    monkeypatch.setattr("axon.router.engine.litellm.acompletion", fake_acompletion)
    return breaker


@pytest.mark.asyncio
async def test_fallback_recovered_usage_as_dict_is_extracted(monkeypatch) -> None:
    """A dict-shaped `usage` on the fallback's response must still yield the
    real token counts, not zeros.
    """
    from axon.router.engine import _mid_tier_model, _top_tier_model

    primary_model = _top_tier_model()
    fallback_model = _mid_tier_model()
    assert primary_model != fallback_model

    def _dict_usage_response(content: str) -> SimpleNamespace:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage={"prompt_tokens": 37, "completion_tokens": 13, "total_tokens": 50},
        )

    async def fake_acompletion(**kwargs):
        if kwargs["model"] == primary_model:
            raise ConnectionError("NIM ConnectError: connection refused")
        return _dict_usage_response("fallback answer")

    _patch_pipeline(monkeypatch, TaskType.ARCHITECTURE, fake_acompletion)

    content, usage = await complete_with_usage(
        TaskRequest(content="design the system", ctx="knowledge"), messages=[]
    )

    assert content == "fallback answer"
    assert isinstance(usage, CompletionUsage)
    assert usage.model == fallback_model
    assert usage.prompt_tokens == 37
    assert usage.completion_tokens == 13
    assert usage.total_tokens == 50


@pytest.mark.asyncio
async def test_fallback_recovered_with_no_usage_attribute_stays_none(monkeypatch) -> None:
    """When the fallback provider genuinely omits usage (no attribute at
    all), the result must still be usage=None.
    """
    from axon.router.engine import _mid_tier_model, _top_tier_model

    primary_model = _top_tier_model()
    fallback_model = _mid_tier_model()
    assert primary_model != fallback_model

    def _no_usage_response(content: str) -> SimpleNamespace:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        )

    async def fake_acompletion(**kwargs):
        if kwargs["model"] == primary_model:
            raise ConnectionError("NIM ConnectError: connection refused")
        return _no_usage_response("fallback answer, no usage")

    _patch_pipeline(monkeypatch, TaskType.ARCHITECTURE, fake_acompletion)

    content, usage = await complete_with_usage(
        TaskRequest(content="design the system", ctx="knowledge"), messages=[]
    )

    assert content == "fallback answer, no usage"
    assert usage is None
