from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

import litellm

from axon.config.runtime import load_runtime_config
from axon.policy.core import PolicyRegistry, ReasonCode
from axon.resilience.circuit_breaker import CircuitBreaker
from axon.resilience.rate_limiter import RateLimiter, spec_from_env
from axon.router.provider_validation import provider_for_model


class TaskType(StrEnum):
    TRIVIAL_COMPLETION = "TRIVIAL_COMPLETION"
    CODE_ANALYSIS = "CODE_ANALYSIS"
    ARCHITECTURE = "ARCHITECTURE"
    DEEP_REASONING = "DEEP_REASONING"
    LOCAL_ONLY = "LOCAL_ONLY"
    UNKNOWN = "UNKNOWN"


_CLASSIFIER_PROMPT = """
Classifique a task em UMA das categorias abaixo. Responda apenas com o nome da categoria.

Categorias:
- TRIVIAL_COMPLETION: autocompletar, snippets curtos, perguntas factuais simples
- CODE_ANALYSIS: revisão de código, debug, refactor, análise de arquitetura existente
- ARCHITECTURE: design de sistema, decisões de arquitetura novas, planejamento de fase
- DEEP_REASONING: raciocínio complexo, trade-offs, múltiplas perspectivas técnicas
- LOCAL_ONLY: apenas informação local, sem necessidade de LLM cloud

Responda apenas com uma das categorias acima, sem texto extra.
"""

_RUNTIME = load_runtime_config()
_POLICY = PolicyRegistry(_RUNTIME)
_BREAKER = CircuitBreaker()
_RATE_LIMITER = RateLimiter()


def _normalize_task_type(raw: str) -> TaskType:
    upper = (raw or "").strip().upper()
    for member in TaskType:
        if member.value in upper:
            return member
    return TaskType.UNKNOWN


def _classify_with_litellm(model: str, content: str) -> TaskType:
    response = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": _CLASSIFIER_PROMPT},
            {"role": "user", "content": content},
        ],
        timeout=_RUNTIME.classifier_timeout_seconds,
        max_tokens=32,
    )
    message = response.choices[0].message.content or ""
    return _normalize_task_type(message)


@lru_cache(maxsize=256)
def _classify_cached(content: str, ctx: str | None) -> tuple[TaskType, str]:
    classifier_model = _RUNTIME.classifier_cloud_model

    decision = _POLICY.decide(
        ctx=ctx,
        model=classifier_model,
        caller="classifier",
        force_cloud=True,
    )
    if not decision.allowed:
        raise RuntimeError(
            "classifier bloqueado pela policy: "
            f"{decision.reason_code.value}"
        )

    provider = provider_for_model(classifier_model)
    rate_spec = spec_from_env(provider)
    if not _RATE_LIMITER.allow_call(provider, rate_spec):
        raise RuntimeError(ReasonCode.DENY_RATE_LIMIT.value)

    breaker_key = f"classifier:{classifier_model}"
    if not _BREAKER.allow_call(breaker_key):
        return TaskType.CODE_ANALYSIS, "fallback"

    try:
        result = _classify_with_litellm(classifier_model, content)
        _BREAKER.record_success(breaker_key)
        return result, "cloud"
    except Exception:
        _BREAKER.record_failure(breaker_key)
        return TaskType.CODE_ANALYSIS, "fallback"


def classify_task_with_source(content: str, ctx: str | None = None) -> tuple[TaskType, str]:
    return _classify_cached(content.strip(), ctx)


def classify_task(content: str, ctx: str | None = None) -> TaskType:
    task_type, _ = classify_task_with_source(content, ctx=ctx)
    return task_type
