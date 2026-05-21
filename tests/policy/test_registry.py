from __future__ import annotations

from types import SimpleNamespace

from axon.policy.core import PolicyRegistry, ReasonCode
from axon.observability.trace_store import TraceStore


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


def test_policy_can_mirror_decision_metadata_into_trace_store(tmp_path) -> None:
    store = TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "data"))
    registry = PolicyRegistry()

    decision = registry.decide(
        ctx="knowledge",
        model="claude-haiku-4-5-20251001",
        caller="router",
        trace_store=store,
        trace_id="trace-321",
        trace_payload={"candidate_docs": 7},
    )

    records = store.load_all()

    assert decision.allowed is True
    assert len(records) == 1
    assert records[0].trace_id == "trace-321"
    assert records[0].stage == "policy"
    assert records[0].caller == "router"
    assert records[0].policy_decision_id == decision.decision_id
    assert records[0].policy_version == decision.policy_version
    assert records[0].route == decision.route.value
    assert records[0].model == decision.model
    assert records[0].payload == {
        "allowed": True,
        "reason_code": "ALLOW_PUBLIC",
        "sensitivity": "PUBLIC",
        "candidate_docs": 7,
    }
