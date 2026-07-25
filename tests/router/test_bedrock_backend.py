"""AWS Bedrock generation backend wired through the existing litellm funnel.

Bedrock is a provider prefix (`bedrock/`) resolved by provider_for_model,
opt-in via AXON_PROVIDER_BEDROCK, with AWS profile/region taken from
AXON-specific env vars so the machine-global AWS_PROFILE is never touched.
The backend inherits breaker, rate limit, budget and usage capture from the
router funnel instead of adding a parallel generation path.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from axon.config.runtime import load_runtime_config
from axon.router.classifier import TaskType
from axon.router.engine import RouteResult, TaskRequest, complete_with_usage

_BEDROCK_MODEL = "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0"


class _FakeBreaker:
    def allow_call(self, _key: str) -> bool:
        return True

    def record_success(self, _key: str) -> None:
        return None

    def record_failure(self, _key: str) -> None:
        return None


def _bedrock_route(_task: TaskRequest) -> RouteResult:
    return RouteResult(
        model=_BEDROCK_MODEL,
        task_type=TaskType.TRIVIAL_COMPLETION,
        estimated_cost=0.0,
        classifier_source="pinned",
        decision_id="test",
        reason_code="test",
        policy_version="test",
    )


def _patch_pipeline(monkeypatch, fake_acompletion, runtime) -> None:
    monkeypatch.setattr("axon.router.engine.route", _bedrock_route)
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 0.0)
    monkeypatch.setattr("axon.router.engine._BREAKER", _FakeBreaker())
    monkeypatch.setattr("axon.router.engine._RUNTIME", runtime)
    monkeypatch.setattr("axon.router.engine.litellm.acompletion", fake_acompletion)


def _fake_acompletion_capturing(sent: dict):
    async def fake_acompletion(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(
                prompt_tokens=1, completion_tokens=1, total_tokens=2
            ),
        )

    return fake_acompletion


def test_runtime_parses_bedrock_env(monkeypatch) -> None:
    monkeypatch.setenv("AXON_PROVIDER_BEDROCK", "1")
    monkeypatch.setenv("AXON_BEDROCK_PROFILE", "axon-bedrock")
    monkeypatch.setenv("AXON_BEDROCK_REGION", "sa-east-1")

    runtime = load_runtime_config()

    assert runtime.provider_bedrock_enabled is True
    assert runtime.bedrock_profile == "axon-bedrock"
    assert runtime.bedrock_region == "sa-east-1"


def test_runtime_bedrock_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("AXON_PROVIDER_BEDROCK", raising=False)
    monkeypatch.delenv("AXON_BEDROCK_PROFILE", raising=False)
    monkeypatch.delenv("AXON_BEDROCK_REGION", raising=False)

    runtime = load_runtime_config()

    assert runtime.provider_bedrock_enabled is False
    assert runtime.bedrock_profile is None
    assert runtime.bedrock_region == "us-east-1"


@pytest.mark.asyncio
async def test_bedrock_disabled_raises_provider_disabled(monkeypatch) -> None:
    from axon.router.engine import _RUNTIME

    runtime = replace(_RUNTIME, provider_bedrock_enabled=False)
    _patch_pipeline(monkeypatch, _fake_acompletion_capturing({}), runtime)

    with pytest.raises(RuntimeError, match="provider disabled: bedrock"):
        await complete_with_usage(TaskRequest(content="q", ctx="knowledge"), messages=[])


@pytest.mark.asyncio
async def test_bedrock_enabled_passes_profile_and_region(monkeypatch) -> None:
    from axon.router.engine import _RUNTIME

    runtime = replace(
        _RUNTIME,
        provider_bedrock_enabled=True,
        bedrock_profile="axon-bedrock",
        bedrock_region="us-east-1",
    )
    sent: dict = {}
    _patch_pipeline(monkeypatch, _fake_acompletion_capturing(sent), runtime)

    content, usage = await complete_with_usage(
        TaskRequest(content="q", ctx="knowledge"), messages=[]
    )

    assert content == "ok"
    assert usage is not None and usage.model == _BEDROCK_MODEL
    assert sent["model"] == _BEDROCK_MODEL
    assert sent["aws_profile_name"] == "axon-bedrock"
    assert sent["aws_region_name"] == "us-east-1"


@pytest.mark.asyncio
async def test_bedrock_availability_reaches_prompt_cache_key(monkeypatch) -> None:
    # The availability string feeds the prompt-layer cache key; if bedrock
    # is missing from it, toggling the provider serves stale system layers.
    from axon.router.engine import _RUNTIME

    runtime = replace(
        _RUNTIME,
        provider_bedrock_enabled=True,
        bedrock_profile=None,
        bedrock_region="us-east-1",
    )
    sent: dict = {}
    _patch_pipeline(monkeypatch, _fake_acompletion_capturing(sent), runtime)

    await complete_with_usage(TaskRequest(content="q", ctx="knowledge"), messages=[])

    semi_static = sent["messages"][1]["content"]
    assert "bedrock=1" in semi_static
