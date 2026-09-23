"""Tests for the pinned TypeSafe benchmark questions."""

from __future__ import annotations

import pytest

from axon.benchmark.typesafe import questions
from axon.recall import strategy


def test_production_similarity_thresholds_are_imported() -> None:
    assert questions.SCOPE_SIM_THRESHOLD == strategy._SCOPE_SIM_THRESHOLD
    assert questions.NEAR_DUP_THRESHOLD == strategy._NEAR_DUP_THRESHOLD


def test_compose_judge_score_maps_both_endpoints() -> None:
    assert questions.compose_judge_score(
        {name: len(levels) - 1 for name, levels in questions.JUDGE_COMPOSITE_LEVELS.items()}
    ) == 5.0
    assert questions.compose_judge_score(
        {name: 0.0 for name in questions.JUDGE_COMPOSITE_LEVELS}
    ) == 0.0


def test_compose_judge_score_rejects_missing_dimension() -> None:
    with pytest.raises(ValueError, match="risk"):
        questions.compose_judge_score({"clarity": 2.0, "completeness": 2.0, "alignment": 2.0})
