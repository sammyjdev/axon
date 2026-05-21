from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

import litellm

from axon.config.runtime import load_runtime_config
from axon.context.cache_key import build_composite_cache_key
from axon.policy.core import PolicyRegistry, ReasonCode
from axon.resilience.circuit_breaker import CircuitBreaker
from axon.router.classifier import TaskType, classify_task_with_source
from axon.router.provider_validation import (
    count_tokens_for_provider,
    provider_for_model,
    validate_anthropic_cache_control,
    validate_openrouter_compliance,
)

logger = logging.getLogger(__name__)
_RUNTIME = load_runtime_config()
_POLICY = PolicyRegistry(_RUNTIME)
_BREAKER = CircuitBreaker(redis_url=_RUNTIME.redis_url)

# ---------------------------------------------------------------------------
# Modelos (D2 — decisão travada)
# ---------------------------------------------------------------------------

_MODEL_MAP: dict[TaskType, str] = {
    TaskType.TRIVIAL_COMPLETION: "claude-haiku-4-5-20251001",
    TaskType.CODE_ANALYSIS: "claude-sonnet-4-6",
    TaskType.ARCHITECTURE: "claude-opus-4-7",
    TaskType.DEEP_REASONING: "claude-opus-4-7",
    TaskType.LOCAL_ONLY: "ollama/phi3:mini",
    TaskType.UNKNOWN: "claude-haiku-4-5-20251001",
}

_BUDGET_USD: float = float(os.environ.get("AXON_DAILY_BUDGET", "5.0"))
_OPUS_BUDGET: float = float(os.environ.get("AXON_OPUS_BUDGET", "2.0"))
_MAX_PRE_SEND_TOKENS: int = int(os.environ.get("AXON_MAX_PRE_SEND_TOKENS", "8000"))

# Custo aproximado por 1k tokens (input+output médio)
_COST_PER_1K: dict[str, float] = {
    "claude-haiku-4-5-20251001": 0.001,
    "claude-sonnet-4-6": 0.01,
    "claude-opus-4-7": 0.05,
    "ollama/phi3:mini": 0.0,
    "ollama/gemma4:e4b": 0.0,
    "ollama/gemma4:26b": 0.0,
}


@dataclass
class TaskRequest:
    content: str
    request_opus: bool = False
    ctx: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class RouteResult:
    model: str
    task_type: TaskType
    estimated_cost: float
    classifier_source: str
    decision_id: str
    reason_code: str
    policy_version: str


_COST_CACHE: dict[str, float] = {"value": 0.0, "at": 0.0}
_PROMPT_CACHE: dict[str, tuple[str, str]] = {}


def daily_cost() -> float:
    """Retorna custo acumulado do dia via Langfuse (fallback: 0.0)."""
    now = time.time()
    if now - _COST_CACHE["at"] <= 60:
        return _COST_CACHE["value"]

    try:
        import httpx

        langfuse_url = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
        r = httpx.get(f"{langfuse_url}/api/public/daily-cost", timeout=1.0)
        if r.status_code == 200:
            _COST_CACHE["value"] = float(r.json().get("cost", 0.0))
            _COST_CACHE["at"] = now
            return _COST_CACHE["value"]
    except Exception:
        pass
    _COST_CACHE["value"] = 0.0
    _COST_CACHE["at"] = now
    return 0.0


def route(task: TaskRequest) -> RouteResult:
    """Classifica a task e retorna o modelo adequado dentro do budget."""
    task_type, source = classify_task_with_source(task.content, ctx=task.ctx)
    cost_today = daily_cost()

    model = _MODEL_MAP.get(task_type, "claude-haiku-4-5-20251001")

    # Downgrade se budget esgotado
    if model == "claude-sonnet-4-6" and cost_today >= _BUDGET_USD:
        model = "claude-haiku-4-5-20251001"

    # Opus só com flag explícito ou dentro do budget específico
    if model == "claude-opus-4-7":
        if not task.request_opus and cost_today >= _OPUS_BUDGET:
            model = "claude-sonnet-4-6"

    decision = _POLICY.decide(
        ctx=task.ctx,
        model=model,
        caller="router",
        force_cloud=bool(task.extra.get("force_cloud")),
    )
    if not decision.allowed:
        raise RuntimeError(f"policy blocked request ({decision.reason_code.value})")

    estimated = _COST_PER_1K.get(model, 0.0) * (len(task.content) / 4000)

    logger.info(
        "router decision: task_type=%s source=%s model=%s cost_today=%.4f estimated=%.6f ctx=%s",
        task_type.value,
        source,
        model,
        cost_today,
        estimated,
        task.ctx or "auto",
    )

    return RouteResult(
        model=model,
        task_type=task_type,
        estimated_cost=estimated,
        classifier_source=source,
        decision_id=decision.decision_id,
        reason_code=decision.reason_code.value,
        policy_version=decision.policy_version,
    )


def _context_layers(task: TaskRequest) -> tuple[str, str, str]:
    availability = (
        f"anthropic={int(_RUNTIME.provider_anthropic_enabled)};"
        f"openrouter={int(_RUNTIME.provider_openrouter_enabled)};"
        f"ollama={int(_RUNTIME.provider_ollama_enabled)}"
    )
    cache_key = build_composite_cache_key(
        content="policy-layers",
        ctx=task.ctx,
        policy_version=_RUNTIME.policy_version,
        model="meta",
        availability=availability,
    )
    cached_layers = _PROMPT_CACHE.get(cache_key)
    if cached_layers:
        static_layer, semi_static = cached_layers
    else:
        static_layer = (
            "Você opera no Prometheus com políticas fixas de isolamento e orçamento. "
            "Nunca exponha contexto corporativo fora de work."
        )
        semi_static = (
            f"ctx={task.ctx or 'auto'}; budget_daily={_BUDGET_USD}; budget_opus={_OPUS_BUDGET}; "
            f"routing=remote-local-cloud; policy_version={_RUNTIME.policy_version}; {availability}"
        )
        _PROMPT_CACHE[cache_key] = (static_layer, semi_static)
    dynamic = task.content
    return static_layer, semi_static, dynamic


async def complete(task: TaskRequest, messages: list[dict]) -> str:
    """Roteia e executa a completion."""
    result = route(task)

    static_layer, semi_static, dynamic = _context_layers(task)
    layered_messages = [
        {"role": "system", "content": static_layer},
        {"role": "system", "content": semi_static},
        {"role": "user", "content": dynamic},
        *messages,
    ]

    provider = provider_for_model(result.model)
    if provider == "anthropic":
        validate_anthropic_cache_control(layered_messages)
    if provider == "openrouter":
        validate_openrouter_compliance(task.extra)

    provider_enabled = {
        "anthropic": _RUNTIME.provider_anthropic_enabled,
        "openrouter": _RUNTIME.provider_openrouter_enabled,
        "ollama": _RUNTIME.provider_ollama_enabled,
    }.get(provider, True)
    if not provider_enabled:
        raise RuntimeError(f"provider disabled: {provider}")

    approx_tokens = count_tokens_for_provider(provider, layered_messages)
    if approx_tokens > _MAX_PRE_SEND_TOKENS:
        raise RuntimeError(ReasonCode.DENY_BUDGET_PRE_SEND.value)

    projected_cost = _COST_PER_1K.get(result.model, 0.0) * (approx_tokens / 1000)
    if result.model == "claude-opus-4-7" and (daily_cost() + projected_cost) > _OPUS_BUDGET:
        result.model = "claude-sonnet-4-6"
    if result.model == "claude-sonnet-4-6" and (daily_cost() + projected_cost) > _BUDGET_USD:
        result.model = "claude-haiku-4-5-20251001"

    breaker_key = f"router:{result.model}"
    if not _BREAKER.allow_call(breaker_key):
        raise RuntimeError(ReasonCode.DENY_BREAKER_OPEN.value)

    try:
        response = await litellm.acompletion(
            model=result.model,
            messages=layered_messages,
        )
        _BREAKER.record_success(breaker_key)
    except Exception:
        _BREAKER.record_failure(breaker_key)
        raise
    return response.choices[0].message.content
