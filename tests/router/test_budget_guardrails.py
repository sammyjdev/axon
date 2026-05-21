from __future__ import annotations

from types import SimpleNamespace

import pytest

import axon.router.engine as engine
from axon.router.classifier import TaskType
from axon.router.engine import TaskRequest, complete, route


def test_route_downgrades_code_analysis_when_daily_budget_is_exhausted(monkeypatch) -> None:
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: engine._BUDGET_USD)

    result = route(TaskRequest(content="analisar diff grande", ctx="knowledge"))

    assert result.model == "claude-haiku-4-5-20251001"


def test_route_downgrades_opus_when_request_is_not_explicit(monkeypatch) -> None:
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.ARCHITECTURE, "local"),
    )
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: engine._OPUS_BUDGET)

    result = route(TaskRequest(content="desenhar arquitetura", ctx="knowledge"))

    assert result.model == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_complete_downgrades_to_haiku_when_projected_cost_crosses_budget(monkeypatch) -> None:
    requested_models: list[str] = []

    async def fake_acompletion(*, model: str, messages: list[dict]):
        requested_models.append(model)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    class FakeBreaker:
        def allow_call(self, _key: str) -> bool:
            return True

        def record_success(self, _key: str) -> None:
            return None

        def record_failure(self, _key: str) -> None:
            return None

    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )
    monkeypatch.setattr(
        "axon.router.engine.daily_cost",
        lambda: engine._BUDGET_USD - 0.005,
    )
    monkeypatch.setattr("axon.router.engine.provider_for_model", lambda _model: "anthropic")
    monkeypatch.setattr(
        "axon.router.engine.validate_anthropic_cache_control", lambda _messages: None
    )
    monkeypatch.setattr(
        "axon.router.engine.count_tokens_for_provider", lambda _provider, _messages: 1000
    )
    monkeypatch.setattr("axon.router.engine._BREAKER", FakeBreaker())
    monkeypatch.setattr("axon.router.engine.litellm.acompletion", fake_acompletion)

    response = await complete(
        TaskRequest(content="investigar comportamento do pipeline", ctx="knowledge"),
        messages=[{"role": "user", "content": "detalhe o risco"}],
    )

    assert response == "ok"
    assert requested_models == ["claude-haiku-4-5-20251001"]
