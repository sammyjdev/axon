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

    def test_rich_commit_body_relaxes_outside_diff_requirement(self) -> None:
        """When the dev's commit body has lexicon hits, the rationale
        does not need fresh architectural terms.

        Regression for the dogfood finding where Portuguese commit
        bodies put 'invariante'/'latência' in the diff token pool,
        burning those terms for the rationale.
        """
        # Rationale's only lexicon hits ('invariant') are also in the diff
        rationale = "Keeps the invariant alive in the rewritten path"
        diff = "diff --git x.py\n+# preserve invariant\n+pass"
        # Commit body has its own lexicon hit ('boundary') outside the diff
        commit_body = (
            "Restored the abstraction boundary between modules so the "
            "single-writer invariant stays sound."
        )
        passed, details = passes_density(
            rationale, diff=diff, commit_body=commit_body
        )
        assert passed is True
        assert details.get("note") == "rich_commit_body_relaxation"
        assert "boundary" in details["body_lex_hits_outside_diff"]

    def test_rich_commit_body_relaxation_requires_lexicon_in_rationale_too(
        self,
    ) -> None:
        """Relaxation only fires if rationale itself has SOME lexicon
        hit. Otherwise it could pass with zero architectural content."""
        rationale = "Just words without any architectural concept here"
        diff = "diff x.py\n+pass"
        commit_body = "Introduces a clean boundary"
        passed, details = passes_density(
            rationale, diff=diff, commit_body=commit_body
        )
        assert passed is False
        assert details["reason"] == "no_architectural_lexicon_outside_diff"

    def test_structural_mode_relaxes_overlap_cap(self) -> None:
        rationale = "adopt repository pattern layer module"
        diff = "adopt repository pattern layer module"
        # In structural mode, cap goes from 0.85 to 0.95
        passed_default, _ = passes_density(rationale, diff=diff)
        passed_struct, _ = passes_density(
            rationale, diff=diff, structural_mode=True
        )
        # Overlap is 1.0 > both caps, so both reject — structural is
        # the looser threshold but still rejects pure copy-paste.
        assert passed_default is False
        assert passed_struct is False
