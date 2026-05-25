from __future__ import annotations

import time

import pytest

from axon.router.classifier import TaskType
from axon.router.engine import TaskRequest, complete, route
from axon.router.profiles import get_profile


def test_route_blocks_cloud_fallback_for_corporate_context() -> None:
    with pytest.raises(RuntimeError, match="DENY_FORCE_CLOUD"):
        route(
            TaskRequest(
                content="analisar modulo de faturamento",
                ctx="work",
                extra={"force_cloud": True},
            )
        )


def test_route_uses_classifier_result_under_free_profile(monkeypatch) -> None:
    free = get_profile("free")
    monkeypatch.setattr(
        "axon.router.engine._MODEL_MAP",
        {task: free.models[task.value] for task in TaskType},
    )
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.TRIVIAL_COMPLETION, "cloud"),
    )
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 0.0)

    result = route(TaskRequest(content="qual comando usar?", ctx="knowledge"))

    assert result.task_type is TaskType.TRIVIAL_COMPLETION
    assert result.model == "groq/llama-3.1-8b-instant"
    assert result.classifier_source == "cloud"
    assert result.reason_code == "ALLOW_PUBLIC"
    assert result.policy_version


def test_route_uses_classifier_result_under_paid_profile(monkeypatch) -> None:
    paid = get_profile("paid")
    monkeypatch.setattr(
        "axon.router.engine._MODEL_MAP",
        {task: paid.models[task.value] for task in TaskType},
    )
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.ARCHITECTURE, "cloud"),
    )
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 0.0)

    result = route(TaskRequest(content="desenhar a arquitetura X", ctx="knowledge"))

    assert result.task_type is TaskType.ARCHITECTURE
    assert result.model == "openrouter/anthropic/claude-opus-4"


def test_top_tier_downgrades_to_mid_when_opus_budget_exceeded(monkeypatch) -> None:
    paid = get_profile("paid")
    monkeypatch.setattr(
        "axon.router.engine._MODEL_MAP",
        {task: paid.models[task.value] for task in TaskType},
    )
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.ARCHITECTURE, "cloud"),
    )
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 999.0)
    monkeypatch.setattr("axon.router.engine._OPUS_BUDGET", 2.0)

    result = route(TaskRequest(content="design system Y", ctx="knowledge"))

    assert result.model == paid.models["CODE_ANALYSIS"]


def test_mid_tier_downgrades_to_bottom_when_daily_budget_exceeded(monkeypatch) -> None:
    paid = get_profile("paid")
    monkeypatch.setattr(
        "axon.router.engine._MODEL_MAP",
        {task: paid.models[task.value] for task in TaskType},
    )
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "cloud"),
    )
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 999.0)
    monkeypatch.setattr("axon.router.engine._BUDGET_USD", 5.0)

    result = route(TaskRequest(content="revisar funcao Z", ctx="knowledge"))

    assert result.model == paid.models["TRIVIAL_COMPLETION"]


def test_request_opus_bypasses_opus_budget_gate(monkeypatch) -> None:
    paid = get_profile("paid")
    monkeypatch.setattr(
        "axon.router.engine._MODEL_MAP",
        {task: paid.models[task.value] for task in TaskType},
    )
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.ARCHITECTURE, "cloud"),
    )
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 999.0)
    monkeypatch.setattr("axon.router.engine._OPUS_BUDGET", 2.0)

    result = route(
        TaskRequest(content="design X", ctx="knowledge", request_opus=True),
    )

    assert result.model == paid.models["ARCHITECTURE"]


@pytest.mark.asyncio
async def test_complete_blocks_when_rate_limiter_denies(monkeypatch) -> None:
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.TRIVIAL_COMPLETION, "cloud"),
    )
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 0.0)

    class DenyingLimiter:
        def allow_call(self, provider, spec):
            return False

    monkeypatch.setattr("axon.router.engine._RATE_LIMITER", DenyingLimiter())

    with pytest.raises(RuntimeError, match="DENY_RATE_LIMIT"):
        await complete(
            TaskRequest(content="oi", ctx="knowledge"),
            messages=[{"role": "user", "content": "x"}],
        )


@pytest.mark.asyncio
async def test_complete_blocks_pre_send_budget(monkeypatch) -> None:
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.TRIVIAL_COMPLETION, "cloud"),
    )
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 0.0)
    monkeypatch.setattr("axon.router.engine._MAX_PRE_SEND_TOKENS", 5)

    with pytest.raises(RuntimeError, match="DENY_BUDGET_PRE_SEND"):
        await complete(
            TaskRequest(content="x" * 500, ctx="knowledge"),
            messages=[{"role": "user", "content": "mensagem extensa"}],
        )


def test_classifier_raises_when_rate_limited(monkeypatch) -> None:
    """Classifier deve levantar DENY_RATE_LIMIT antes de chamar LiteLLM."""
    import axon.router.classifier as classifier

    # Conteudo unico pra escapar do lru_cache (256 entries shared entre testes).
    unique_content = f"classifier_rate_limit_probe_{time.time()}"

    class DenyingLimiter:
        def allow_call(self, provider, spec):
            return False

    monkeypatch.setattr(classifier, "_RATE_LIMITER", DenyingLimiter())

    with pytest.raises(RuntimeError, match="DENY_RATE_LIMIT"):
        classifier.classify_task_with_source(unique_content, ctx="knowledge")


def test_unknown_profile_raises() -> None:
    with pytest.raises(ValueError, match="profile invalido"):
        get_profile("ultra-paid")


def test_profiles_define_all_task_types() -> None:
    for profile_name in ("free", "paid"):
        profile = get_profile(profile_name)
        for task in TaskType:
            assert task.value in profile.models, (
                f"profile {profile_name} sem mapping pra {task.value}"
            )
