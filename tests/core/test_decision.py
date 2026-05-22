"""Tests for the Decision domain model."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from axon.core.decision import Decision
from axon.exceptions import ValidationError as AxonValidationError


def _decision(**overrides: Any) -> Decision:
    base: dict[str, Any] = dict(
        id="dec-001",
        timestamp=datetime(2026, 5, 21, 12, 0, tzinfo=UTC),
        agent="claude-code",
        repo="axon",
        summary="rename prometheus to axon",
    )
    base.update(overrides)
    return Decision(**base)


def test_minimal_decision_has_defaults() -> None:
    d = _decision()
    assert d.status == "draft"
    assert d.validation_score == 0.0
    assert d.files == [] and d.symbols == [] and d.tags == []
    assert d.git_hash is None


@pytest.mark.parametrize("bad_id", ["dec-1", "dec-12", "decision-001", "001", "DEC-001"])
def test_id_regex_rejects_bad_ids(bad_id: str) -> None:
    with pytest.raises(PydanticValidationError):
        _decision(id=bad_id)


@pytest.mark.parametrize("good_id", ["dec-001", "dec-1234", "dec-100"])
def test_id_regex_accepts_good_ids(good_id: str) -> None:
    assert _decision(id=good_id).id == good_id


@pytest.mark.parametrize("score", [-0.1, 5.1, 10.0])
def test_validation_score_out_of_range_rejected(score: float) -> None:
    with pytest.raises(PydanticValidationError):
        _decision(validation_score=score)


def test_summary_max_length_enforced() -> None:
    with pytest.raises(PydanticValidationError):
        _decision(summary="x" * 81)


@pytest.mark.parametrize("bad_tag", ["UPPER", "has space", "trailing-", "-leading", "a--b"])
def test_tags_must_be_kebab_case(bad_tag: str) -> None:
    with pytest.raises(PydanticValidationError):
        _decision(tags=[bad_tag])


def test_tags_accepts_kebab_case() -> None:
    d = _decision(tags=["rename", "phase-0", "axon-mcp"])
    assert d.tags == ["rename", "phase-0", "axon-mcp"]


def test_naive_timestamp_coerced_to_utc() -> None:
    d = _decision(timestamp=datetime(2026, 5, 21, 12, 0))
    assert d.timestamp.utcoffset() == UTC.utcoffset(None)


def test_decision_is_frozen() -> None:
    d = _decision()
    with pytest.raises(PydanticValidationError):
        d.summary = "changed"  # type: ignore[misc]


def test_from_markdown_without_frontmatter_raises_axon_error() -> None:
    with pytest.raises(AxonValidationError):
        Decision.from_markdown("just some text, no frontmatter")


def test_markdown_round_trip_explicit() -> None:
    d = _decision(
        files=[Path("src/axon/core/decision.py")],
        symbols=["axon.core.decision.Decision"],
        tags=["phase-1"],
        validation_score=4.5,
        git_hash="abc1234",
        status="active",
    )
    assert Decision.from_markdown(d.to_markdown()) == d


_ids = st.from_regex(r"dec-\d{3,6}", fullmatch=True)

_decisions = st.builds(
    Decision,
    id=_ids,
    timestamp=st.datetimes(
        min_value=datetime(2000, 1, 1),
        max_value=datetime(2100, 1, 1),
        timezones=st.just(UTC),
    ),
    agent=st.sampled_from(["claude-code", "codex", "cursor", "manual"]),
    repo=st.from_regex(r"[a-z][a-z0-9_-]{0,20}", fullmatch=True),
    files=st.lists(
        st.from_regex(r"[a-z]{1,6}(/[a-z]{1,6}){0,2}", fullmatch=True).map(Path),
        max_size=4,
    ),
    symbols=st.lists(st.from_regex(r"[a-z][a-z0-9_.]{0,30}", fullmatch=True), max_size=4),
    summary=st.text(
        alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E),
        max_size=80,
    ).map(str.strip),
    validation_score=st.floats(min_value=0.0, max_value=5.0, allow_nan=False),
    git_hash=st.none() | st.from_regex(r"[0-9a-f]{7,40}", fullmatch=True),
    linked_decisions=st.lists(_ids, max_size=3),
    tags=st.lists(st.from_regex(r"[a-z0-9]+(-[a-z0-9]+)*", fullmatch=True), max_size=4),
    status=st.sampled_from(["draft", "active", "superseded", "deprecated"]),
)


@given(_decisions)
def test_markdown_round_trip_property(decision: Decision) -> None:
    assert Decision.from_markdown(decision.to_markdown()) == decision
