"""Tests for file kind mapping in axon.core.file_identity."""

from __future__ import annotations

from typing import get_args

import pytest

from axon.core.file_identity import FILE_KINDS, RESERVED_KINDS, Kind, kind_for_path

KIND_TABLE_CASES: list[tuple[str, str]] = [
    ("docs/ADR.md", "adr"),
    ("docs/adr/0001.md", "adr"),
    ("adrs/0002.md", "adr"),
    ("docs/decisions/dec-106-x.md", "adr"),
    ("docs/ARD.md", "ard"),
    ("docs/ard/req.md", "ard"),
    ("PRD.md", "prd"),
    ("docs/prd/x.md", "prd"),
    ("src/prd.py", "prd"),
    ("specs/adr/x.md", "adr"),
    (".specs/features/x/spec.md", "spec"),
    ("docs/superpowers/plans/x.md", "spec"),
    ("README.md", "doc"),
    ("notes.txt", "doc"),
    ("docs/g.rst", "doc"),
    ("src/main.py", "code"),
    ("src/Main.java", "code"),
]


@pytest.mark.parametrize("path,expected", KIND_TABLE_CASES)
def test_kind_table_maps_every_path_to_its_kind(path: str, expected: str) -> None:
    """Every path in the canonical table maps to its expected kind."""
    assert kind_for_path(path) == expected


def test_the_adr_rule_wins_over_the_spec_rule() -> None:
    """ADR rule precedes spec rule in first-match order."""
    assert kind_for_path("specs/adr/x.md") == "adr"


def test_a_non_dec_file_under_decisions_is_a_doc() -> None:
    """Files under decisions must match dec-*.md to become an adr."""
    assert kind_for_path("docs/decisions/README.md") == "doc"


def test_kind_is_case_insensitive_per_segment() -> None:
    """Casing does not affect segment or stem matching."""
    assert kind_for_path("docs/Adr.md") == "adr"
    assert kind_for_path("src/PRD.py") == "prd"


def test_kind_works_on_a_path_with_no_file_behind_it() -> None:
    """Kind is derived from path text alone with no disk probe (ID-5)."""
    assert kind_for_path("docs/ADR.md") == "adr"


def test_the_kind_type_reserves_three_values_no_function_returns() -> None:
    """Kind reserves decision, lesson, and divergence, but no file gets them (ID-6)."""
    assert set(get_args(Kind)) == FILE_KINDS | RESERVED_KINDS
    assert RESERVED_KINDS == {"decision", "lesson", "divergence"}
    for path, _ in KIND_TABLE_CASES:
        assert kind_for_path(path) not in RESERVED_KINDS


def test_kind_never_reads_chunk_type_or_language() -> None:
    """Kind depends on path location and extension, not chunk type or language."""
    assert kind_for_path("src/main.java") == "code"
    assert kind_for_path("docs/notes.md") == "doc"
    assert kind_for_path("src/main.py") == "code"
    assert kind_for_path("notes.txt") == "doc"
