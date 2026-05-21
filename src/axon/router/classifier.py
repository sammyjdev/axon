from __future__ import annotations

from enum import Enum
from functools import lru_cache

import litellm
import ollama

from axon.config.runtime import load_runtime_config
from axon.policy.core import PolicyRegistry
from axon.resilience.circuit_breaker import CircuitBreaker


class TaskType(str, Enum):
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
_BREAKER = CircuitBreaker(redis_url=_RUNTIME.redis_url)


def _normalize_task_type(raw: str) -> TaskType:
    upper = (raw or "").strip().upper()
    for member in TaskType:
        if member.value in upper:
            return member
    return TaskType.UNKNOWN


def _classify_with_ollama(host: str, content: str) -> TaskType:
    client = ollama.Client(host=host)
    response = client.chat(
        model="phi3:mini",
        messages=[
            {"role": "system", "content": _CLASSIFIER_PROMPT},
            {"role": "user", "content": content},
        ],
    )
    return _normalize_task_type(response["message"]["content"])


def _classify_with_cloud(content: str) -> TaskType:
    response = litellm.completion(
        model=_RUNTIME.classifier_cloud_model,
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
    hosts: list[tuple[str, str]] = []
    if _RUNTIME.ollama_remote_host:
        hosts.append(("remote", _RUNTIME.ollama_remote_host))
    hosts.append(("local", _RUNTIME.ollama_local_host))

    for source, host in hosts:
        breaker_key = f"classifier:ollama:{host}"
        if not _BREAKER.allow_call(breaker_key):
            continue
        try:
            result = _classify_with_ollama(host, content)
            _BREAKER.record_success(breaker_key)
            return result, source
        except Exception:
            _BREAKER.record_failure(breaker_key)
            continue

    cloud_model = _RUNTIME.classifier_cloud_model
    cloud_decision = _POLICY.decide(
        ctx=ctx,
        model=cloud_model,
        caller="classifier",
        force_cloud=True,
    )
    if not cloud_decision.allowed:
        raise RuntimeError(
            "classifier local indisponivel e fallback cloud bloqueado pela policy: "
            f"{cloud_decision.reason_code.value}"
        )

    breaker_key = f"classifier:cloud:{cloud_model}"
    if not _BREAKER.allow_call(breaker_key):
        return TaskType.CODE_ANALYSIS, "fallback"

    try:
        result = _classify_with_cloud(content)
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
