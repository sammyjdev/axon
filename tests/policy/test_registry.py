from __future__ import annotations

from prometheus.policy.core import PolicyRegistry, ReasonCode


def test_policy_allows_public_cloud_for_non_work_ctx() -> None:
    registry = PolicyRegistry()
    decision = registry.decide(
        ctx="knowledge",
        model="claude-haiku-4-5-20251001",
        caller="router",
    )

    assert decision.allowed is True
    assert decision.reason_code is ReasonCode.ALLOW_PUBLIC


def test_policy_denies_force_cloud_in_work_ctx() -> None:
    registry = PolicyRegistry()
    decision = registry.decide(
        ctx="work",
        model="claude-haiku-4-5-20251001",
        caller="router",
        force_cloud=True,
    )

    assert decision.allowed is False
    assert decision.reason_code is ReasonCode.DENY_FORCE_CLOUD


def test_policy_allows_local_for_work_ctx() -> None:
    registry = PolicyRegistry()
    decision = registry.decide(
        ctx="work",
        model="ollama/phi3:mini",
        caller="router",
    )

    assert decision.allowed is True
    assert decision.reason_code is ReasonCode.ALLOW_LOCAL
