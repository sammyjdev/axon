from __future__ import annotations

from axon.observability.friction import aggregate_friction
from axon.observability.trace_store import TraceRecord


def _record(**overrides: object) -> TraceRecord:
    values: dict[str, object] = {
        "trace_id": "trace-1",
        "stage": "policy",
        "caller": "mcp.axon_capture",
        "ts": "2026-07-01T10:00:00+00:00",
        "ctx": "personal",
        "payload": {"allowed": False, "reason_code": "DENY_RESTRICTED"},
    }
    values.update(overrides)
    return TraceRecord(**values)


def test_aggregates_denials_across_distinct_days() -> None:
    patterns = aggregate_friction(
        [_record(), _record(ts="2026-07-02T12:00:00+00:00")]
    )

    assert len(patterns) == 1
    assert patterns[0].count == 2
    assert patterns[0].distinct_days == 2
    assert patterns[0].last_ts == "2026-07-02T12:00:00+00:00"


def test_filters_denials_seen_on_one_day() -> None:
    assert aggregate_friction([_record(), _record(ts="2026-07-01T12:00:00+00:00")]) == []


def test_ignores_allowed_policy_records_and_other_stages() -> None:
    patterns = aggregate_friction(
        [
            _record(payload={"allowed": True, "reason_code": "DENY_RESTRICTED"}),
            _record(stage="error"),
            _record(ts="2026-07-02T10:00:00+00:00"),
        ]
    )

    assert patterns == []


def test_skips_records_missing_reason_code() -> None:
    patterns = aggregate_friction(
        [
            _record(payload={"allowed": False}),
            _record(
                ts="2026-07-02T10:00:00+00:00",
                payload={"allowed": False},
            ),
        ]
    )

    assert patterns == []


def test_sorts_patterns_by_distinct_days_then_count() -> None:
    patterns = aggregate_friction(
        [
            _record(caller="mcp.first"),
            _record(caller="mcp.first", ts="2026-07-02T10:00:00+00:00"),
            _record(caller="mcp.second"),
            _record(caller="mcp.second", ts="2026-07-02T10:00:00+00:00"),
            _record(caller="mcp.second", ts="2026-07-03T10:00:00+00:00"),
        ]
    )

    assert [pattern.caller for pattern in patterns] == ["mcp.second", "mcp.first"]
