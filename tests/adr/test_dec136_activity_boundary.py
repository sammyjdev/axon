"""Failing check for dec-136: the operational-activity/memory boundary ADR."""

from __future__ import annotations

from pathlib import Path

DEC_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs" / "decisions" / "dec-136-operational-activity-history.md"
)


def _text() -> str:
    assert DEC_PATH.exists(), f"missing {DEC_PATH}"
    return DEC_PATH.read_text()


def test_dec136_exists_and_is_discoverable_from_adr_index() -> None:
    text = _text()
    assert "dec-136" in text.lower()
    adr_index = (DEC_PATH.parents[1] / "ADR.md").read_text()
    assert "dec-136" in adr_index


def test_dec136_states_operational_records_are_not_semantic_memory() -> None:
    text = _text().lower()
    assert "durable evidence" in text
    assert "not semantic memory" in text or "not memory" in text


def test_dec136_states_explicit_link_rule() -> None:
    text = _text().lower()
    assert "explicit" in text and "link" in text
    assert "infer" in text and "used" in text


def test_dec136_states_no_timestamp_causality_rule() -> None:
    text = _text().lower()
    assert "timestamp" in text
    assert "cannot prove causality" in text or "not causality" in text or "never causality" in text


def test_dec136_states_retention_redaction_coverage_and_local_only() -> None:
    text = _text().lower()
    for term in ("retention", "redaction", "coverage", "local"):
        assert term in text, f"missing term: {term}"


def test_dec136_states_v1_exclusions() -> None:
    text = _text().lower()
    assert "v1" in text
    assert "exclu" in text  # exclusion / excluded


def test_dec136_states_rollback_preserves_existing_sanitized_activity() -> None:
    text = _text().lower()
    assert "rollback" in text
    assert "stops new ingestion" in text or "stop new ingestion" in text
    assert "does not delete" in text
