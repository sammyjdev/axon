from __future__ import annotations

from prometheus.context.contracts import (
    DEFAULT_RETRIEVAL_STRATEGIES,
    ContextPack,
    select_default_retrieval_strategy,
)
from prometheus.context.registry import DEFAULT_SEARCH_CONTEXTS
from prometheus.router.classifier import TaskType


def test_context_pack_exposes_joined_text_and_strategy_contract() -> None:
    strategy = DEFAULT_RETRIEVAL_STRATEGIES["balanced"]
    pack = ContextPack(
        strategy=strategy,
        task_type=TaskType.CODE_ANALYSIS.value,
        profile="solo-dev",
        mode="hybrid-local",
        contexts=("knowledge",),
        segments=("first hit", "second hit"),
    )

    assert pack.text == "first hit\n\nsecond hit"
    assert pack.strategy.name == "balanced"
    assert pack.contexts == ("knowledge",)


def test_selector_prefers_minimal_strategy_for_privacy_first_profile() -> None:
    strategy = select_default_retrieval_strategy(
        task_type=TaskType.ARCHITECTURE,
        profile="privacy-first",
        mode="minimal",
        capabilities=("local-rag", "lean-context", "offline-first"),
    )

    assert strategy.name == "minimal"
    assert strategy.prefer_local is True
    assert strategy.enable_compression is False
    assert strategy.contexts == DEFAULT_SEARCH_CONTEXTS


def test_selector_prefers_local_strategy_for_local_only_tasks() -> None:
    strategy = select_default_retrieval_strategy(
        task_type=TaskType.LOCAL_ONLY,
        profile="team-dev",
        mode="remote-infra",
        capabilities=("shared-remote-infra",),
    )

    assert strategy.name == "local"
    assert strategy.prefer_local is True
    assert strategy.enable_compression is True


def test_selector_prefers_deep_strategy_for_architecture_full_local() -> None:
    strategy = select_default_retrieval_strategy(
        task_type="ARCHITECTURE",
        profile="solo-dev",
        mode="full-local",
        capabilities=("heavy-local-models", "local-rag"),
    )

    assert strategy.name == "deep"
    assert strategy.max_segments > DEFAULT_RETRIEVAL_STRATEGIES["balanced"].max_segments


def test_selector_prefers_balanced_strategy_for_remote_team_mode() -> None:
    strategy = select_default_retrieval_strategy(
        task_type="UNKNOWN",
        profile="team-dev",
        mode="remote-infra",
        capabilities=("shared-remote-infra",),
    )

    assert strategy.name == "balanced"
    assert strategy.prefer_local is False
