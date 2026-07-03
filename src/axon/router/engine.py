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
from axon.resilience.rate_limiter import RateLimiter, spec_from_env
from axon.router.classifier import TaskType, classify_task_with_source
from axon.router.profiles import get_profile
from axon.router.provider_validation import (
    count_tokens_for_provider,
    provider_for_model,
    validate_anthropic_cache_control,
    validate_openrouter_compliance,
)

logger = logging.getLogger(__name__)
_RUNTIME = load_runtime_config()
_POLICY = PolicyRegistry(_RUNTIME)
_BREAKER = CircuitBreaker()
_RATE_LIMITER = RateLimiter()

# Mapping task -> model é selecionado pelo profile ativo (free | paid).
# D2 (Haiku/Sonnet/Opus) é preservado pelo profile PAID via OpenRouter.
_PROFILE = get_profile(_RUNTIME.provider_profile)
_MODEL_MAP: dict[TaskType, str] = {
    task: _PROFILE.models[task.value] for task in TaskType
}

_BUDGET_USD: float = float(os.environ.get("AXON_DAILY_BUDGET", "5.0"))
_OPUS_BUDGET: float = float(os.environ.get("AXON_OPUS_BUDGET", "2.0"))
_MAX_PRE_SEND_TOKENS: int = int(os.environ.get("AXON_MAX_PRE_SEND_TOKENS", "8000"))

_COST_PER_1K: dict[str, float] = dict(_PROFILE.cost_per_1k)


def _top_tier_model() -> str:
    return _MODEL_MAP[TaskType.ARCHITECTURE]


def _mid_tier_model() -> str:
    return _MODEL_MAP[TaskType.CODE_ANALYSIS]


def _bottom_tier_model() -> str:
    return _MODEL_MAP[TaskType.TRIVIAL_COMPLETION]


@dataclass(frozen=True)
class CompletionUsage:
    """Real token usage reported by the provider for one completion."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


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
    """Classifica a task e retorna o modelo adequado dentro do budget.

    Quando AXON_COMPLETION_MODEL está definido, a classificação e o downgrade
    de budget são ignorados — o operador fixou o modelo explicitamente.
    """
    pinned_model = os.environ.get("AXON_COMPLETION_MODEL", "").strip()
    if pinned_model:
        decision = _POLICY.decide(
            ctx=task.ctx,
            model=pinned_model,
            caller="router",
            force_cloud=bool(task.extra.get("force_cloud")),
        )
        if not decision.allowed:
            raise RuntimeError(f"policy blocked request ({decision.reason_code.value})")

        estimated = _COST_PER_1K.get(pinned_model, 0.0) * (len(task.content) / 4000)

        logger.info(
            "router decision: task_type=%s source=%s model=%s estimated=%.6f ctx=%s",
            TaskType.UNKNOWN.value,
            "pinned",
            pinned_model,
            estimated,
            task.ctx or "auto",
        )

        return RouteResult(
            model=pinned_model,
            task_type=TaskType.UNKNOWN,
            estimated_cost=estimated,
            classifier_source="pinned",
            decision_id=decision.decision_id,
            reason_code=decision.reason_code.value,
            policy_version=decision.policy_version,
        )

    task_type, source = classify_task_with_source(task.content, ctx=task.ctx)
    cost_today = daily_cost()

    model = _MODEL_MAP.get(task_type, _bottom_tier_model())
    model = _maybe_downgrade_for_budget(task_type, model, cost_today, task.request_opus)

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


def _maybe_downgrade_for_budget(
    task_type: TaskType,
    model: str,
    cost_today: float,
    request_opus: bool,
) -> str:
    """Tier downgrade por budget, agnostico ao profile.

    Top-tier (ARCHITECTURE/DEEP_REASONING) cai pra mid-tier quando estoura
    `_OPUS_BUDGET` (a menos que `request_opus=True` force).
    Mid-tier (CODE_ANALYSIS) cai pra bottom-tier quando estoura `_BUDGET_USD`.
    """
    if task_type in (TaskType.ARCHITECTURE, TaskType.DEEP_REASONING):
        if not request_opus and cost_today >= _OPUS_BUDGET:
            return _mid_tier_model()
    if task_type is TaskType.CODE_ANALYSIS and cost_today >= _BUDGET_USD:
        return _bottom_tier_model()
    return model


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
            "Você opera no AXON com políticas fixas de isolamento e orçamento. "
            "Nunca exponha contexto corporativo fora de work."
        )
        semi_static = (
            f"ctx={task.ctx or 'auto'}; budget_daily={_BUDGET_USD}; budget_opus={_OPUS_BUDGET}; "
            f"routing=remote-local-cloud; policy_version={_RUNTIME.policy_version}; {availability}"
        )
        _PROMPT_CACHE[cache_key] = (static_layer, semi_static)
    dynamic = task.content
    return static_layer, semi_static, dynamic


async def complete_with_usage(
    task: TaskRequest, messages: list[dict]
) -> tuple[str, CompletionUsage | None]:
    """Roteia e executa a completion, retornando o usage real do provider."""
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
    if provider == "openrouter" and _RUNTIME.openrouter_compliance_required:
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
    if result.task_type in (TaskType.ARCHITECTURE, TaskType.DEEP_REASONING):
        if not task.request_opus and (daily_cost() + projected_cost) > _OPUS_BUDGET:
            result.model = _mid_tier_model()
    if result.task_type is TaskType.CODE_ANALYSIS:
        if (daily_cost() + projected_cost) > _BUDGET_USD:
            result.model = _bottom_tier_model()

    # Recomputa provider apos eventual downgrade (Opus->Sonnet pode cruzar provedores).
    provider = provider_for_model(result.model)
    rate_spec = spec_from_env(provider)
    if not _RATE_LIMITER.allow_call(provider, rate_spec):
        raise RuntimeError(ReasonCode.DENY_RATE_LIMIT.value)

    breaker_key = f"router:{result.model}"
    if not _BREAKER.allow_call(breaker_key):
        raise RuntimeError(ReasonCode.DENY_BREAKER_OPEN.value)

    # Para modelos Ollama, litellm precisa do api_base — resolve via env ou
    # AXON_OLLAMA_LOCAL_HOST. Não afeta provedores cloud.
    completion_kwargs: dict = {"model": result.model, "messages": layered_messages}
    if result.model.startswith("ollama/"):
        ollama_host = (
            os.environ.get("OLLAMA_BASE_URL")
            or _RUNTIME.ollama_local_host
            or "http://localhost:11434"
        )
        completion_kwargs["api_base"] = ollama_host

    try:
        response = await litellm.acompletion(**completion_kwargs)
        _BREAKER.record_success(breaker_key)
    except Exception:
        _BREAKER.record_failure(breaker_key)
        raise
    content = response.choices[0].message.content
    raw_usage = getattr(response, "usage", None)
    usage: CompletionUsage | None = None
    if raw_usage is not None:
        usage = CompletionUsage(
            model=result.model,
            prompt_tokens=int(getattr(raw_usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(raw_usage, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(raw_usage, "total_tokens", 0) or 0),
        )
    return content, usage


async def complete(task: TaskRequest, messages: list[dict]) -> str:
    """Roteia e executa a completion (compat wrapper, discards usage)."""
    content, _usage = await complete_with_usage(task, messages)
    return content
