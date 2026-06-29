"""Tests for AXON_COMPLETION_MODEL override (pinned-model feature).

Verifies:
1. When AXON_COMPLETION_MODEL is set, route() uses it verbatim and
   classify_task_with_source is NOT called.
2. When the env var is unset, routing behaves exactly as before (classifier
   is called, budget downgrade applies).
3. complete() passes api_base only for ollama/ models, not for cloud models.
"""
from __future__ import annotations

import pytest

from axon.router.classifier import TaskType
from axon.router.engine import TaskRequest, complete, route
from axon.router.profiles import get_profile

# ---------------------------------------------------------------------------
# route() with AXON_COMPLETION_MODEL set
# ---------------------------------------------------------------------------


def test_route_pinned_model_bypasses_classifier(monkeypatch) -> None:
    """With AXON_COMPLETION_MODEL set, route() must use that model and must
    NOT invoke classify_task_with_source (which would fail without Groq)."""
    monkeypatch.setenv("AXON_COMPLETION_MODEL", "ollama/qwen2.5:7b")

    def _should_not_be_called(content: str, ctx=None):
        raise AssertionError("classify_task_with_source was called despite AXON_COMPLETION_MODEL")

    monkeypatch.setattr("axon.router.engine.classify_task_with_source", _should_not_be_called)

    result = route(TaskRequest(content="what is recursion?", ctx="knowledge"))

    assert result.model == "ollama/qwen2.5:7b"
    assert result.classifier_source == "pinned"
    assert result.task_type is TaskType.UNKNOWN


def test_route_pinned_model_skips_budget_downgrade(monkeypatch) -> None:
    """Budget downgrade logic is skipped when the model is pinned."""
    monkeypatch.setenv("AXON_COMPLETION_MODEL", "ollama/qwen2.5:7b")
    # Force both budget thresholds exceeded
    monkeypatch.setattr("axon.router.engine._OPUS_BUDGET", 0.0)
    monkeypatch.setattr("axon.router.engine._BUDGET_USD", 0.0)
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 999.0)
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (_ for _ in ()).throw(
            AssertionError("classifier must not be called")
        ),
    )

    result = route(TaskRequest(content="explain closures", ctx="knowledge"))

    # Model must NOT be downgraded to any profile tier
    assert result.model == "ollama/qwen2.5:7b"


def test_route_pinned_model_non_ollama_provider(monkeypatch) -> None:
    """Pinned model works for any litellm provider string, not just ollama."""
    monkeypatch.setenv(
        "AXON_COMPLETION_MODEL", "nvidia_nim/meta/llama-3.1-70b-instruct"
    )
    monkeypatch.setattr(
        "axon.router.engine.classify_task_with_source",
        lambda content, ctx=None: (_ for _ in ()).throw(
            AssertionError("classifier must not be called")
        ),
    )

    result = route(TaskRequest(content="design a microservice", ctx="knowledge"))

    assert result.model == "nvidia_nim/meta/llama-3.1-70b-instruct"
    assert result.classifier_source == "pinned"


# ---------------------------------------------------------------------------
# route() without AXON_COMPLETION_MODEL — existing behavior unchanged
# ---------------------------------------------------------------------------


def test_route_normal_uses_classifier_when_env_unset(monkeypatch) -> None:
    """When AXON_COMPLETION_MODEL is unset, routing uses the classifier and
    maps the result through _MODEL_MAP as before."""
    monkeypatch.delenv("AXON_COMPLETION_MODEL", raising=False)

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

    result = route(TaskRequest(content="what is 2+2?", ctx="knowledge"))

    assert result.task_type is TaskType.TRIVIAL_COMPLETION
    assert result.model == "groq/llama-3.1-8b-instant"
    assert result.classifier_source == "cloud"


def test_route_normal_budget_downgrade_still_works(monkeypatch) -> None:
    """When env var is unset, budget downgrade still applies normally."""
    monkeypatch.delenv("AXON_COMPLETION_MODEL", raising=False)

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

    result = route(TaskRequest(content="design system X", ctx="knowledge"))

    assert result.model == paid.models["CODE_ANALYSIS"]


# ---------------------------------------------------------------------------
# complete() — ollama api_base wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_passes_api_base_for_ollama_model(monkeypatch) -> None:
    """complete() must include api_base when model starts with 'ollama/'."""
    monkeypatch.setenv("AXON_COMPLETION_MODEL", "ollama/qwen2.5:7b")
    monkeypatch.setenv("AXON_PROVIDER_OLLAMA", "1")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 0.0)

    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        class _Msg:
            content = "ok"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()

    monkeypatch.setattr("axon.router.engine.litellm.acompletion", fake_acompletion)

    # Provide a mock runtime with the local host configured
    import axon.router.engine as engine_mod
    from axon.config.runtime import RuntimeConfig

    original_runtime = engine_mod._RUNTIME
    # Build a minimal replacement that only overrides ollama_local_host
    fake_runtime = RuntimeConfig(
        mode=original_runtime.mode,
        engine_root=original_runtime.engine_root,
        vault_root=original_runtime.vault_root,
        db_path=original_runtime.db_path,
        pg_url=original_runtime.pg_url,
        redis_url=original_runtime.redis_url,
        rtk_max_tokens=original_runtime.rtk_max_tokens,
        caveman_num_ctx=original_runtime.caveman_num_ctx,
        ollama_remote_host=original_runtime.ollama_remote_host,
        ollama_local_host="http://localhost:11434",
        caveman_model=original_runtime.caveman_model,
        scoring_model=original_runtime.scoring_model,
        scoring_num_ctx=original_runtime.scoring_num_ctx,
        classifier_cloud_model=original_runtime.classifier_cloud_model,
        classifier_timeout_seconds=original_runtime.classifier_timeout_seconds,
        policy_version=original_runtime.policy_version,
        provider_anthropic_enabled=original_runtime.provider_anthropic_enabled,
        provider_openrouter_enabled=original_runtime.provider_openrouter_enabled,
        provider_ollama_enabled=True,
        provider_profile=original_runtime.provider_profile,
        openrouter_compliance_required=original_runtime.openrouter_compliance_required,
        expansion=original_runtime.expansion,
        active_profile=original_runtime.active_profile,
    )
    monkeypatch.setattr("axon.router.engine._RUNTIME", fake_runtime)

    await complete(
        TaskRequest(content="hello", ctx="knowledge"),
        messages=[],
    )

    assert "api_base" in captured, "api_base must be passed for ollama/ models"
    assert captured["api_base"] == "http://localhost:11434"
    assert captured["model"] == "ollama/qwen2.5:7b"


@pytest.mark.asyncio
async def test_complete_no_api_base_for_cloud_model(monkeypatch) -> None:
    """complete() must NOT include api_base for non-ollama cloud models."""
    monkeypatch.setenv("AXON_COMPLETION_MODEL", "openrouter/anthropic/claude-haiku-4")
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 0.0)

    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        class _Msg:
            content = "ok"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()

    monkeypatch.setattr("axon.router.engine.litellm.acompletion", fake_acompletion)

    await complete(
        TaskRequest(content="hello", ctx="knowledge"),
        messages=[],
    )

    assert "api_base" not in captured, "api_base must NOT be set for cloud models"
    assert captured["model"] == "openrouter/anthropic/claude-haiku-4"


@pytest.mark.asyncio
async def test_complete_ollama_uses_ollama_base_url_env(monkeypatch) -> None:
    """OLLAMA_BASE_URL env var takes precedence over AXON_OLLAMA_LOCAL_HOST."""
    monkeypatch.setenv("AXON_COMPLETION_MODEL", "ollama/qwen2.5:7b")
    monkeypatch.setenv("AXON_PROVIDER_OLLAMA", "1")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://192.168.1.50:11434")
    monkeypatch.setattr("axon.router.engine.daily_cost", lambda: 0.0)

    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        class _Msg:
            content = "ok"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()

    monkeypatch.setattr("axon.router.engine.litellm.acompletion", fake_acompletion)

    import axon.router.engine as engine_mod
    from axon.config.runtime import RuntimeConfig

    original_runtime = engine_mod._RUNTIME
    fake_runtime = RuntimeConfig(
        mode=original_runtime.mode,
        engine_root=original_runtime.engine_root,
        vault_root=original_runtime.vault_root,
        db_path=original_runtime.db_path,
        pg_url=original_runtime.pg_url,
        redis_url=original_runtime.redis_url,
        rtk_max_tokens=original_runtime.rtk_max_tokens,
        caveman_num_ctx=original_runtime.caveman_num_ctx,
        ollama_remote_host=original_runtime.ollama_remote_host,
        ollama_local_host="http://localhost:11434",
        caveman_model=original_runtime.caveman_model,
        scoring_model=original_runtime.scoring_model,
        scoring_num_ctx=original_runtime.scoring_num_ctx,
        classifier_cloud_model=original_runtime.classifier_cloud_model,
        classifier_timeout_seconds=original_runtime.classifier_timeout_seconds,
        policy_version=original_runtime.policy_version,
        provider_anthropic_enabled=original_runtime.provider_anthropic_enabled,
        provider_openrouter_enabled=original_runtime.provider_openrouter_enabled,
        provider_ollama_enabled=True,
        provider_profile=original_runtime.provider_profile,
        openrouter_compliance_required=original_runtime.openrouter_compliance_required,
        expansion=original_runtime.expansion,
        active_profile=original_runtime.active_profile,
    )
    monkeypatch.setattr("axon.router.engine._RUNTIME", fake_runtime)

    await complete(
        TaskRequest(content="hello", ctx="knowledge"),
        messages=[],
    )

    assert captured["api_base"] == "http://192.168.1.50:11434"
