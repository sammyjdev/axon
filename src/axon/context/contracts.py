from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from axon.context.registry import DEFAULT_SEARCH_CONTEXTS

TaskTypeName = Literal[
    "TRIVIAL_COMPLETION",
    "CODE_ANALYSIS",
    "ARCHITECTURE",
    "DEEP_REASONING",
    "LOCAL_ONLY",
    "UNKNOWN",
]
RuntimeModeName = Literal["full-local", "hybrid-local", "remote-infra", "minimal"]


@dataclass(frozen=True)
class RetrievalStrategy:
    name: str
    contexts: tuple[str, ...]
    max_segments: int
    max_chars: int
    prefer_local: bool
    enable_compression: bool


@dataclass(frozen=True)
class ContextPack:
    strategy: RetrievalStrategy
    task_type: str
    profile: str | None
    mode: str | None
    contexts: tuple[str, ...]
    segments: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def text(self) -> str:
        return "\n\n".join(segment for segment in self.segments if segment)


DEFAULT_RETRIEVAL_STRATEGIES: dict[str, RetrievalStrategy] = {
    "minimal": RetrievalStrategy(
        name="minimal",
        contexts=DEFAULT_SEARCH_CONTEXTS,
        max_segments=4,
        max_chars=4_000,
        prefer_local=True,
        enable_compression=False,
    ),
    "local": RetrievalStrategy(
        name="local",
        contexts=DEFAULT_SEARCH_CONTEXTS,
        max_segments=6,
        max_chars=6_000,
        prefer_local=True,
        enable_compression=True,
    ),
    "balanced": RetrievalStrategy(
        name="balanced",
        contexts=DEFAULT_SEARCH_CONTEXTS,
        max_segments=8,
        max_chars=8_000,
        prefer_local=False,
        enable_compression=True,
    ),
    "deep": RetrievalStrategy(
        name="deep",
        contexts=DEFAULT_SEARCH_CONTEXTS,
        max_segments=12,
        max_chars=12_000,
        prefer_local=False,
        enable_compression=True,
    ),
}

_DEEP_TASK_TYPES = {"ARCHITECTURE", "DEEP_REASONING"}


def select_default_retrieval_strategy(
    *,
    task_type: TaskTypeName | Enum | str,
    profile: str | None = None,
    mode: RuntimeModeName | str | None = None,
    capabilities: tuple[str, ...] | list[str] = (),
) -> RetrievalStrategy:
    normalized_task_type = _normalize_task_type(task_type)
    normalized_profile = _normalize_optional(profile)
    normalized_mode = _normalize_optional(mode)
    normalized_capabilities = {_normalize_optional(cap) for cap in capabilities}
    normalized_capabilities.discard(None)

    if normalized_task_type == "LOCAL_ONLY":
        return DEFAULT_RETRIEVAL_STRATEGIES["local"]

    if (
        normalized_mode == "minimal"
        or normalized_profile == "privacy-first"
        or "lean-context" in normalized_capabilities
    ):
        return DEFAULT_RETRIEVAL_STRATEGIES["minimal"]

    if "offline-first" in normalized_capabilities:
        return DEFAULT_RETRIEVAL_STRATEGIES["local"]

    if normalized_task_type in _DEEP_TASK_TYPES and (
        normalized_mode == "full-local" or "heavy-local-models" in normalized_capabilities
    ):
        return DEFAULT_RETRIEVAL_STRATEGIES["deep"]

    if (
        normalized_mode == "remote-infra"
        or normalized_profile == "team-dev"
        or "shared-remote-infra" in normalized_capabilities
    ):
        return DEFAULT_RETRIEVAL_STRATEGIES["balanced"]

    if normalized_task_type == "TRIVIAL_COMPLETION":
        return DEFAULT_RETRIEVAL_STRATEGIES["minimal"]

    if normalized_task_type == "CODE_ANALYSIS":
        return DEFAULT_RETRIEVAL_STRATEGIES["balanced"]

    if normalized_task_type in _DEEP_TASK_TYPES:
        return DEFAULT_RETRIEVAL_STRATEGIES["deep"]

    return DEFAULT_RETRIEVAL_STRATEGIES["balanced"]


def _normalize_task_type(task_type: TaskTypeName | Enum | str) -> str:
    raw = getattr(task_type, "value", task_type)
    return str(raw or "").strip().upper() or "UNKNOWN"


def _normalize_optional(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None
