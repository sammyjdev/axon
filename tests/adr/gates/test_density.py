"""Tests for the density gate (dec-111)."""

from __future__ import annotations

from axon.adr.gates.density import passes_density


class TestDensity:
    def test_passes_with_lexicon_hit_outside_diff(self) -> None:
        rationale = "We adopt this pattern to decouple modules"
        diff = "src/x.py: added Foo class"
        passed, details = passes_density(rationale, diff=diff)
        assert passed is True
        assert "adopt" in details.get("lex_hits_outside_diff", [])

    def test_fails_when_no_lexicon_hit_at_all(self) -> None:
        rationale = "Some words here without keywords"
        diff = "src/x.py: bytes"
        passed, details = passes_density(rationale, diff=diff)
        assert passed is False
        assert details["reason"] == "no_architectural_lexicon_outside_diff"

    def test_fails_when_lexicon_only_in_diff(self) -> None:
        # Lexicon token present in both rationale AND diff — does NOT count
        rationale = "refactor refactor refactor"
        diff = "we refactor everything"
        passed, details = passes_density(rationale, diff=diff)
        assert passed is False

    def test_fails_when_overlap_ratio_exceeds_cap(self) -> None:
        # Rationale is 100% present in diff → reject
        rationale = "adopt repository pattern layer module"
        diff = "adopt repository pattern layer module"
        passed, details = passes_density(
            rationale, diff=diff, overlap_ratio_cap=0.5
        )
        assert passed is False
        assert details["reason"] == "overlap_ratio_exceeds_cap"

    def test_structural_mode_relaxes_lexicon_requirement(self) -> None:
        rationale = "Move x to y to z"
        diff = "rename x to y"
        passed, details = passes_density(
            rationale, diff=diff, structural_mode=True
        )
        assert passed is True
        assert details["structural_mode"] is True

    def test_structural_mode_relaxes_overlap_cap(self) -> None:
        rationale = "adopt repository pattern layer module"
        diff = "adopt repository pattern layer module"
        # In structural mode, cap goes from 0.7 to 0.9
        passed_default, _ = passes_density(rationale, diff=diff)
        passed_struct, _ = passes_density(
            rationale, diff=diff, structural_mode=True
        )
        assert passed_default is False
        # Overlap is 1.0 > 0.9, so still fails — but at higher threshold
        assert passed_struct is False
