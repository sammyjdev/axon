from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"
BASELINE_PATH = Path(__file__).parent / "baseline.json"

REQUIRED_QUERY_KEYS = {"id", "query", "expected_file", "expected_symbol", "min_score"}


def test_golden_set_schema() -> None:
    """Golden set file exists and each entry has all required keys."""
    assert GOLDEN_SET_PATH.exists(), "golden_set.json missing"
    data = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    assert len(data) == 20, f"expected 20 queries, got {len(data)}"
    for entry in data:
        missing = REQUIRED_QUERY_KEYS - set(entry.keys())
        assert not missing, f"entry {entry.get('id')} missing keys: {missing}"
        assert isinstance(entry["min_score"], float)
        assert 0.0 < entry["min_score"] <= 1.0


def test_golden_set_no_duplicate_ids() -> None:
    data = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    ids = [e["id"] for e in data]
    assert len(ids) == len(set(ids)), "duplicate IDs in golden_set.json"


def test_baseline_json_exists() -> None:
    assert BASELINE_PATH.exists(), "baseline.json missing"
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert "recall_top1" in data
    assert "recall_top3" in data
    assert "results_by_query" in data


@pytest.mark.skip(reason="Full harness activated after Task 4 chunker changes are stable")
def test_recall_guard_no_regression() -> None:
    """Activated in Task 4: asserts regressions == [] and score >= 0.90."""
    ...
