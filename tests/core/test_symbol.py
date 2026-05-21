"""Tests for the Symbol domain model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from axon.core.symbol import Symbol


def _symbol(**overrides: Any) -> Symbol:
    base: dict[str, Any] = dict(
        id="axon.core.decision.Decision",
        type="class",
        file=Path("src/axon/core/decision.py"),
        start_line=10,
        end_line=80,
        language="python",
    )
    base.update(overrides)
    return Symbol(**base)


def test_valid_symbol() -> None:
    s = _symbol()
    assert s.id == "axon.core.decision.Decision"
    assert s.type == "class"
    assert s.language == "python"


@pytest.mark.parametrize("bad_type", ["module", "variable", "Class", ""])
def test_invalid_symbol_type_rejected(bad_type: str) -> None:
    with pytest.raises(PydanticValidationError):
        _symbol(type=bad_type)


@pytest.mark.parametrize("bad_language", ["typescript", "go", "Python"])
def test_invalid_language_rejected(bad_language: str) -> None:
    with pytest.raises(PydanticValidationError):
        _symbol(language=bad_language)


def test_end_line_before_start_line_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _symbol(start_line=80, end_line=10)


def test_zero_line_numbers_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _symbol(start_line=0)


def test_single_line_symbol_allowed() -> None:
    s = _symbol(start_line=42, end_line=42)
    assert s.start_line == s.end_line == 42


def test_symbol_is_frozen() -> None:
    s = _symbol()
    with pytest.raises(PydanticValidationError):
        s.id = "other"  # type: ignore[misc]
