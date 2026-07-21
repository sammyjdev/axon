from __future__ import annotations

import time
from types import SimpleNamespace

import litellm
import pytest

from axon.router.classifier import TaskType


def _resp(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _allow_all(*_args: object, **_kwargs: object) -> bool:
    return True


def _noop(*_args: object, **_kwargs: object) -> None:
    return None


def _policy_allow(**kw) -> SimpleNamespace:
    return SimpleNamespace(allowed=True)


def test_litellm_drop_params_is_true() -> None:
    """AC #1: litellm.drop_params deve ser True apos import do classifier."""
    import axon.router.classifier  # noqa: F401 - module-level setattr

    assert litellm.drop_params is True


def test_classifier_passes_reasoning_effort_low(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC #2: _classify_with_litellm passa reasoning_effort='low'."""
    import axon.router.classifier as M

    monkeypatch.setattr(M._POLICY, "decide", _policy_allow)
    monkeypatch.setattr(M._RATE_LIMITER, "allow_call", _allow_all)
    monkeypatch.setattr(M._BREAKER, "allow_call", _allow_all)
    monkeypatch.setattr(M._BREAKER, "record_success", _noop)

    captured: dict = {}

    def fake_completion(**kw: object) -> object:
        captured.update(kw)
        return _resp("TRIVIAL_COMPLETION")

    monkeypatch.setattr("axon.router.classifier.litellm.completion", fake_completion)

    unique = f"reasoning_effort_test_{time.time()}"
    result, source = M.classify_task_with_source(unique, ctx="knowledge")

    assert captured.get("reasoning_effort") == "low"
    assert result is TaskType.TRIVIAL_COMPLETION
    assert source == "cloud"


def test_classifier_resolves_category_when_reasoning_effort_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #3: regression - classifier retorna categoria real (nao UNKNOWN)
    quando reasoning_effort='low' esta presente, provando que a inanicacao
    por reasoning-token starvation foi corrigida."""
    import axon.router.classifier as M

    monkeypatch.setattr(M._POLICY, "decide", _policy_allow)
    monkeypatch.setattr(M._RATE_LIMITER, "allow_call", _allow_all)
    monkeypatch.setattr(M._BREAKER, "allow_call", _allow_all)
    monkeypatch.setattr(M._BREAKER, "record_success", _noop)

    def conditional_completion(**kw: object) -> object:
        if kw.get("reasoning_effort") == "low":
            return _resp("ARCHITECTURE")
        return _resp("")

    monkeypatch.setattr("axon.router.classifier.litellm.completion", conditional_completion)

    unique = f"regression_test_{time.time()}"
    result, source = M.classify_task_with_source(unique, ctx="knowledge")

    assert result is TaskType.ARCHITECTURE
    assert result is not TaskType.UNKNOWN
    assert source == "cloud"


def test_classifier_works_with_non_reasoning_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #4: modelo nao-reasoning ainda funciona - drop_params silencia o
    parametro nao suportado e a completude retorna categoria normal."""
    import axon.router.classifier as M

    monkeypatch.setattr(M._POLICY, "decide", _policy_allow)
    monkeypatch.setattr(M._RATE_LIMITER, "allow_call", _allow_all)
    monkeypatch.setattr(M._BREAKER, "allow_call", _allow_all)
    monkeypatch.setattr(M._BREAKER, "record_success", _noop)

    def normal_completion(**kw: object) -> object:
        return _resp("CODE_ANALYSIS")

    monkeypatch.setattr("axon.router.classifier.litellm.completion", normal_completion)

    unique = f"non_reasoning_test_{time.time()}"
    result, source = M.classify_task_with_source(unique, ctx="knowledge")

    assert result is TaskType.CODE_ANALYSIS
    assert source == "cloud"


def test_non_reasoning_model_accepts_new_call_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import axon.router.classifier as M

    def non_reasoning_completion(**kw: object) -> object:
        assert kw.get("reasoning_effort") == "low"
        if not litellm.drop_params:
            raise RuntimeError("reasoning_effort is unsupported")
        return _resp("TRIVIAL_COMPLETION")

    monkeypatch.setattr(M.litellm, "completion", non_reasoning_completion)

    result = M._classify_with_litellm(
        "groq/llama-3.1-8b-instant",
        "What is two plus two?",
    )

    assert result is TaskType.TRIVIAL_COMPLETION
