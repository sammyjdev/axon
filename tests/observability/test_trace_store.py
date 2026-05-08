from __future__ import annotations

from types import SimpleNamespace

from prometheus.observability.trace_store import TraceRecord, TraceStore


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
