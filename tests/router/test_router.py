from __future__ import annotations

import pytest

from prometheus.router.classifier import TaskType
from prometheus.router.engine import TaskRequest, complete, route


def test_route_blocks_cloud_fallback_for_corporate_context() -> None:
    with pytest.raises(RuntimeError, match="DENY_FORCE_CLOUD"):
        route(
            TaskRequest(
                content="analisar modulo de faturamento",
                ctx="work",
                extra={"force_cloud": True},
            )
        )


def test_route_uses_classifier_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "prometheus.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.TRIVIAL_COMPLETION, "local"),
    )
    monkeypatch.setattr("prometheus.router.engine.daily_cost", lambda: 0.0)

    result = route(TaskRequest(content="qual comando usar?", ctx="knowledge"))

    assert result.task_type is TaskType.TRIVIAL_COMPLETION
    assert result.model == "claude-haiku-4-5-20251001"
    assert result.classifier_source == "local"
    assert result.reason_code == "ALLOW_PUBLIC"
    assert result.policy_version


@pytest.mark.asyncio
async def test_complete_blocks_pre_send_budget(monkeypatch) -> None:
    monkeypatch.setattr(
        "prometheus.router.engine.classify_task_with_source",
        lambda content, ctx=None: (TaskType.TRIVIAL_COMPLETION, "local"),
    )
    monkeypatch.setattr("prometheus.router.engine.daily_cost", lambda: 0.0)
    monkeypatch.setattr("prometheus.router.engine._MAX_PRE_SEND_TOKENS", 5)

    with pytest.raises(RuntimeError, match="DENY_BUDGET_PRE_SEND"):
        await complete(
            TaskRequest(content="x" * 500, ctx="knowledge"),
            messages=[{"role": "user", "content": "mensagem extensa"}],
        )
