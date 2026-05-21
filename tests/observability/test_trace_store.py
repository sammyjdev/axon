from __future__ import annotations

from types import SimpleNamespace

from axon.policy.core import (
    PolicyDecision,
    ReasonCode,
    RouteType,
    SensitivityLevel,
)
from axon.observability.trace_store import TraceRecord, TraceStore


def test_trace_store_load_all_returns_empty_when_file_is_missing(tmp_path) -> None:
    store = TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "data"))

    assert store.load_all() == []


def test_trace_store_appends_and_loads_correlation_records(tmp_path) -> None:
    store = TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "data"))
    records = [
        TraceRecord(
            trace_id="trace-123",
            stage="retrieval",
            caller="cli",
            ts="2026-05-08T12:00:00+00:00",
            ctx="knowledge",
            payload={"chunks": 3, "strategy": "default"},
        ),
        TraceRecord(
            trace_id="trace-123",
            stage="compression",
            caller="cli",
            ts="2026-05-08T12:00:01+00:00",
            ctx="knowledge",
            model="phi3:mini",
            payload={"before_tokens": 400, "after_tokens": 210},
        ),
        TraceRecord(
            trace_id="trace-123",
            stage="policy",
            caller="router",
            ts="2026-05-08T12:00:02+00:00",
            ctx="knowledge",
            policy_decision_id="decision-1",
            policy_version="2026-04-21",
            route="cloud",
            model="claude-sonnet-4-6",
            payload={"allowed": True},
        ),
        TraceRecord(
            trace_id="trace-123",
            stage="output",
            caller="mcp",
            ts="2026-05-08T12:00:03+00:00",
            ctx="knowledge",
            policy_decision_id="decision-1",
            payload={"status": "ok"},
        ),
    ]

    for record in records:
        store.append(record)

    assert store.records_file == tmp_path / "data" / "trace" / "records.jsonl"
    assert store.load_all() == records


def test_trace_store_query_filters_by_trace_id_and_stage(tmp_path) -> None:
    store = TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "data"))
    records = [
        TraceRecord(
            trace_id="trace-123",
            stage="retrieval",
            caller="cli",
            ts="2026-05-08T12:00:00+00:00",
            ctx="knowledge",
        ),
        TraceRecord(
            trace_id="trace-123",
            stage="compression",
            caller="cli",
            ts="2026-05-08T12:00:01+00:00",
            ctx="knowledge",
        ),
        TraceRecord(
            trace_id="trace-999",
            stage="compression",
            caller="cli",
            ts="2026-05-08T12:00:02+00:00",
            ctx="knowledge",
        ),
    ]
    for record in records:
        store.append(record)

    result = store.query(trace_id="trace-123", stage="compression")

    assert result == [records[1]]


def test_trace_store_query_filters_by_policy_decision_and_caller(tmp_path) -> None:
    store = TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "data"))
    records = [
        TraceRecord(
            trace_id="trace-123",
            stage="policy",
            caller="router",
            ts="2026-05-08T12:00:00+00:00",
            ctx="knowledge",
            policy_decision_id="decision-1",
        ),
        TraceRecord(
            trace_id="trace-123",
            stage="output",
            caller="router",
            ts="2026-05-08T12:00:01+00:00",
            ctx="knowledge",
            policy_decision_id="decision-1",
        ),
        TraceRecord(
            trace_id="trace-123",
            stage="policy",
            caller="classifier",
            ts="2026-05-08T12:00:02+00:00",
            ctx="knowledge",
            policy_decision_id="decision-1",
        ),
        TraceRecord(
            trace_id="trace-123",
            stage="policy",
            caller="router",
            ts="2026-05-08T12:00:03+00:00",
            ctx="work",
            policy_decision_id="decision-2",
        ),
    ]
    for record in records:
        store.append(record)

    result = store.query(
        policy_decision_id="decision-1",
        caller="router",
        ctx="knowledge",
    )

    assert result == [records[0], records[1]]


def test_trace_store_recorder_appends_stages_with_shared_defaults(tmp_path) -> None:
    store = TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "data"))
    recorder = store.recorder(trace_id="trace-456", caller="router", ctx="knowledge")

    retrieval = recorder.append_stage(
        "retrieval",
        payload={"chunks": 4},
    )
    compression = recorder.append_stage(
        "compression",
        model="phi3:mini",
        payload={"before_tokens": 120, "after_tokens": 80},
    )

    assert retrieval.trace_id == "trace-456"
    assert retrieval.caller == "router"
    assert retrieval.ctx == "knowledge"
    assert compression.model == "phi3:mini"
    assert store.load_all() == [retrieval, compression]


def test_trace_store_recorder_mirrors_policy_decision_metadata(tmp_path) -> None:
    store = TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "data"))
    recorder = store.recorder(trace_id="trace-789", caller="router", ctx="knowledge")
    decision = PolicyDecision(
        decision_id="decision-9",
        allowed=False,
        reason_code=ReasonCode.DENY_CONFIDENTIAL_CLOUD,
        policy_version="2026-04-21",
        route=RouteType.CLOUD,
        model="claude-sonnet-4-6",
        ctx="knowledge",
        sensitivity=SensitivityLevel.CONFIDENTIAL,
        metadata={"policy_path": "retrieval.route"},
    )

    record = recorder.append_policy_decision(
        decision,
        payload={"blocked_docs": 2},
    )

    assert record.policy_decision_id == "decision-9"
    assert record.policy_version == "2026-04-21"
    assert record.route == "cloud"
    assert record.model == "claude-sonnet-4-6"
    assert record.payload == {
        "allowed": False,
        "reason_code": "DENY_CONFIDENTIAL_CLOUD",
        "sensitivity": "CONFIDENTIAL",
        "policy_path": "retrieval.route",
        "blocked_docs": 2,
    }
