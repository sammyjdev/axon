from __future__ import annotations

import os
from dataclasses import dataclass, field

import litellm

from prometheus.router.classifier import TaskType, classify_task

# ---------------------------------------------------------------------------
# Modelos (D2 — decisão travada)
# ---------------------------------------------------------------------------

_MODEL_MAP: dict[TaskType, str] = {
    TaskType.TRIVIAL_COMPLETION: "claude-haiku-4-5-20251001",
    TaskType.CODE_ANALYSIS:      "claude-sonnet-4-6",
    TaskType.ARCHITECTURE:       "claude-opus-4-7",
    TaskType.DEEP_REASONING:     "claude-opus-4-7",
    TaskType.LOCAL_ONLY:         "ollama/phi3:mini",
    TaskType.UNKNOWN:            "claude-haiku-4-5-20251001",
}

_BUDGET_USD: float = float(os.environ.get("PROMETHEUS_DAILY_BUDGET", "5.0"))
_OPUS_BUDGET: float = float(os.environ.get("PROMETHEUS_OPUS_BUDGET", "2.0"))

# Custo aproximado por 1k tokens (input+output médio)
_COST_PER_1K: dict[str, float] = {
    "claude-haiku-4-5-20251001": 0.001,
    "claude-sonnet-4-6":         0.01,
    "claude-opus-4-7":           0.05,
    "ollama/phi3:mini":          0.0,
    "ollama/gemma4:e4b":         0.0,
    "ollama/gemma4:26b":         0.0,
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


def daily_cost() -> float:
    """Retorna custo acumulado do dia via Langfuse (fallback: 0.0)."""
    try:
        import httpx
        langfuse_url = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
        r = httpx.get(f"{langfuse_url}/api/public/daily-cost", timeout=1.0)
        if r.status_code == 200:
            return float(r.json().get("cost", 0.0))
    except Exception:
        pass
    return 0.0


def route(task: TaskRequest) -> RouteResult:
    """Classifica a task e retorna o modelo adequado dentro do budget."""
    task_type = classify_task(task.content)
    cost_today = daily_cost()

    model = _MODEL_MAP.get(task_type, "claude-haiku-4-5-20251001")

    # Downgrade se budget esgotado
    if model == "claude-sonnet-4-6" and cost_today >= _BUDGET_USD:
        model = "claude-haiku-4-5-20251001"

    # Opus só com flag explícito ou dentro do budget específico
    if model == "claude-opus-4-7":
        if not task.request_opus and cost_today >= _OPUS_BUDGET:
            model = "claude-sonnet-4-6"

    estimated = _COST_PER_1K.get(model, 0.0) * (len(task.content) / 4000)

    return RouteResult(model=model, task_type=task_type, estimated_cost=estimated)


async def complete(task: TaskRequest, messages: list[dict]) -> str:
    """Roteia e executa a completion."""
    result = route(task)
    response = await litellm.acompletion(
        model=result.model,
        messages=messages,
    )
    return response.choices[0].message.content
