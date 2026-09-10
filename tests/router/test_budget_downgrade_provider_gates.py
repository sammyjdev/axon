from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

import axon.router.engine as engine
from axon.router.classifier import TaskType
from axon.router.engine import TaskRequest, complete_with_usage
from axon.router.profiles import get_profile
from axon.router.provider_validation import provider_for_model

_BUDGET = get_profile("budget")
_BUDGET_TOP = _BUDGET.models["ARCHITECTURE"]
_BUDGET_MID = _BUDGET.models["CODE_ANALYSIS"]
_BUDGET_BOTTOM = _BUDGET.models["TRIVIAL_COMPLETION"]
_ANTHROPIC_TOP = "claude-opus-4"
_ANTHROPIC_MID = "claude-sonnet-4"
_BAD_CACHE_CONTROL = [{"role": "user", "content": "prior", "cache_control": "bad"}]
_COMPLIANCE = {"zdr": True, "retention": "none", "training_use": False}


class _Permissive:
    def allow_call(self, *_args: object) -> bool:
        return True

    def record_success(self, _key: str) -> None:
        pass

    def record_failure(self, _key: str) -> None:
        pass


def _setup_downgrade(
    monkeypatch: pytest.MonkeyPatch,
    *,
    task_type: TaskType,
    routed_model: str,
    final_model: str,
    compliance_required: bool = False,
    openrouter_enabled: bool = True,
) -> list[str]:
    model_map = {item: final_model for item in TaskType}
    model_map[task_type] = routed_model
    monkeypatch.delenv("AXON_COMPLETION_MODEL", raising=False)
    monkeypatch.setenv(
        "AXON_OPENROUTER_COMPLIANCE", "1" if compliance_required else "0"
    )
    monkeypatch.setenv(
        "AXON_PROVIDER_OPENROUTER", "1" if openrouter_enabled else "0"
    )
    monkeypatch.setattr(engine, "_MODEL_MAP", model_map)
    monkeypatch.setattr(
        engine,
        "classify_task_with_source",
        lambda content, ctx=None: (task_type, "local"),
    )
    monkeypatch.setattr(engine, "_OPUS_BUDGET", 2.0)
    monkeypatch.setattr(engine, "_BUDGET_USD", 5.0)
    monkeypatch.setattr(engine, "_MAX_PRE_SEND_TOKENS", 8000)
    monkeypatch.setattr(engine, "count_tokens_for_provider", lambda _p, _m: 100)

    reads = 0

    def _daily_cost() -> float:
        nonlocal reads
        reads += 1
        return 0.0 if reads == 1 else 100.0

    monkeypatch.setattr(engine, "daily_cost", _daily_cost)
    monkeypatch.setattr(
        engine,
        "_RUNTIME",
        dataclasses.replace(
            engine._RUNTIME,
            openrouter_compliance_required=compliance_required,
            provider_openrouter_enabled=openrouter_enabled,
            provider_anthropic_enabled=True,
        ),
    )
    monkeypatch.setattr(engine, "_RATE_LIMITER", _Permissive())
    monkeypatch.setattr(engine, "_BREAKER", _Permissive())

    sent: list[str] = []

    async def _fake_completion(model: str, _messages: list[dict]) -> object:
        sent.append(model)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="sent"))],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
        )

    monkeypatch.setattr(engine, "_call_completion", _fake_completion)
    return sent


@pytest.mark.asyncio
async def test_architecture_downgrade_requires_openrouter_compliance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert provider_for_model(_BUDGET_TOP) == "deepinfra"
    assert provider_for_model(_BUDGET_MID) == "openrouter"
    sent = _setup_downgrade(
        monkeypatch,
        task_type=TaskType.ARCHITECTURE,
        routed_model=_BUDGET_TOP,
        final_model=_BUDGET_MID,
        compliance_required=True,
    )

    with pytest.raises(ValueError, match="openrouter requer metadata de compliance"):
        await complete_with_usage(
            TaskRequest(content="design the system", ctx="knowledge"), messages=[]
        )

    assert sent == []


@pytest.mark.asyncio
async def test_architecture_downgrade_refuses_disabled_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _setup_downgrade(
        monkeypatch,
        task_type=TaskType.ARCHITECTURE,
        routed_model=_BUDGET_TOP,
        final_model=_BUDGET_MID,
        openrouter_enabled=False,
    )

    with pytest.raises(RuntimeError, match="provider disabled: openrouter"):
        await complete_with_usage(
            TaskRequest(content="design the system", ctx="knowledge"), messages=[]
        )

    assert sent == []


@pytest.mark.asyncio
async def test_architecture_downgrade_sends_when_openrouter_gates_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _setup_downgrade(
        monkeypatch,
        task_type=TaskType.ARCHITECTURE,
        routed_model=_BUDGET_TOP,
        final_model=_BUDGET_MID,
        compliance_required=True,
    )

    content, usage = await complete_with_usage(
        TaskRequest(
            content="design the system",
            ctx="knowledge",
            extra=dict(_COMPLIANCE),
        ),
        messages=[],
    )

    assert sent == [_BUDGET_MID]
    assert content == "sent"
    assert usage is not None and usage.model == _BUDGET_MID


@pytest.mark.asyncio
async def test_code_analysis_downgrade_requires_final_provider_compliance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert provider_for_model(_BUDGET_TOP) == "deepinfra"
    assert provider_for_model(_BUDGET_MID) == "openrouter"
    sent = _setup_downgrade(
        monkeypatch,
        task_type=TaskType.CODE_ANALYSIS,
        routed_model=_BUDGET_TOP,
        final_model=_BUDGET_MID,
        compliance_required=True,
    )

    with pytest.raises(ValueError, match="openrouter requer metadata de compliance"):
        await complete_with_usage(
            TaskRequest(content="review this code", ctx="knowledge"), messages=[]
        )

    assert sent == []


@pytest.mark.asyncio
async def test_downgrade_away_from_openrouter_skips_original_provider_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert provider_for_model(_BUDGET_MID) == "openrouter"
    assert provider_for_model(_BUDGET_BOTTOM) == "deepinfra"
    sent = _setup_downgrade(
        monkeypatch,
        task_type=TaskType.CODE_ANALYSIS,
        routed_model=_BUDGET_MID,
        final_model=_BUDGET_BOTTOM,
        compliance_required=True,
        openrouter_enabled=False,
    )

    content, _usage = await complete_with_usage(
        TaskRequest(content="review this code", ctx="knowledge"), messages=[]
    )

    assert sent == [_BUDGET_BOTTOM]
    assert content == "sent"


@pytest.mark.asyncio
async def test_downgrade_onto_anthropic_validates_cache_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert provider_for_model(_BUDGET_TOP) == "deepinfra"
    assert provider_for_model(_ANTHROPIC_MID) == "anthropic"
    sent = _setup_downgrade(
        monkeypatch,
        task_type=TaskType.ARCHITECTURE,
        routed_model=_BUDGET_TOP,
        final_model=_ANTHROPIC_MID,
    )

    with pytest.raises(ValueError, match="cache_control"):
        await complete_with_usage(
            TaskRequest(content="design the system", ctx="knowledge"),
            messages=list(_BAD_CACHE_CONTROL),
        )

    assert sent == []


@pytest.mark.asyncio
async def test_downgrade_away_from_anthropic_skips_cache_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert provider_for_model(_ANTHROPIC_TOP) == "anthropic"
    assert provider_for_model(_BUDGET_TOP) == "deepinfra"
    sent = _setup_downgrade(
        monkeypatch,
        task_type=TaskType.ARCHITECTURE,
        routed_model=_ANTHROPIC_TOP,
        final_model=_BUDGET_TOP,
    )

    content, _usage = await complete_with_usage(
        TaskRequest(content="design the system", ctx="knowledge"),
        messages=list(_BAD_CACHE_CONTROL),
    )

    assert sent == [_BUDGET_TOP]
    assert content == "sent"
