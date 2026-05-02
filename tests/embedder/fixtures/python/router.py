from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModelTier(str, Enum):
    HAIKU = "claude-haiku-4-5-20251001"
    SONNET = "claude-sonnet-4-6"
    OPUS = "claude-opus-4-7"
    LOCAL = "ollama/phi3:mini"


@dataclass
class RoutingDecision:
    model: ModelTier
    reason: str
    estimated_cost_usd: float


def route(query: str, daily_spent: float, budget: float) -> RoutingDecision:
    if daily_spent >= budget:
        return RoutingDecision(ModelTier.HAIKU, "budget_exceeded", 0.001)

    if len(query) < 50:
        return RoutingDecision(ModelTier.HAIKU, "short_query", 0.001)

    if _is_architecture_query(query):
        return RoutingDecision(ModelTier.OPUS, "architecture", 0.05)

    return RoutingDecision(ModelTier.SONNET, "code_analysis", 0.01)


def _is_architecture_query(query: str) -> bool:
    keywords = {"design", "architecture", "pattern", "trade-off", "adr"}
    return any(k in query.lower() for k in keywords)


def estimate_tokens(text: str) -> int:
    return len(text) // 4
