"""Tests for the Edge domain model."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from axon.core.edge import Edge


def _edge(**overrides: Any) -> Edge:
    base: dict[str, Any] = dict(
        source_id="dec-001",
        target_id="axon.core.decision.Decision",
        type="touches",
    )
    base.update(overrides)
    return Edge(**base)


def test_valid_edge_defaults_payload_to_none() -> None:
    e = _edge()
    assert e.source_id == "dec-001"
    assert e.payload is None


@pytest.mark.parametrize(
    "edge_type",
    ["touches", "calls", "imports", "supersedes", "discussed_in", "committed_as"],
)
def test_all_edge_types_accepted(edge_type: str) -> None:
    assert _edge(type=edge_type).type == edge_type


@pytest.mark.parametrize("bad_type", ["uses", "extends", "Touches", ""])
def test_invalid_edge_type_rejected(bad_type: str) -> None:
    with pytest.raises(PydanticValidationError):
        _edge(type=bad_type)


def test_payload_round_trips() -> None:
    e = _edge(type="committed_as", payload={"git_hash": "abc1234", "lines": 12})
    assert e.payload == {"git_hash": "abc1234", "lines": 12}


def test_edge_is_frozen() -> None:
    e = _edge()
    with pytest.raises(PydanticValidationError):
        e.type = "calls"  # type: ignore[misc]
