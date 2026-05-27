"""Tests for L2 lexical and L3 polarity gates (dec-111)."""

from __future__ import annotations

from axon.adr.gates.l2 import overlap_count, passes_l2, tokenize
from axon.adr.gates.l3 import passes_l3


class TestTokenize:
    def test_lowercases_and_drops_stopwords(self) -> None:
        tokens = tokenize("The Repository pattern decouples Storage from Logic")
        assert "the" not in tokens
        assert "from" not in tokens
        assert "repository" in tokens

    def test_drops_short_tokens(self) -> None:
        assert "ab" not in tokenize("ab xy ok")

    def test_strips_jira_ids(self) -> None:
        tokens = tokenize("JIRA-1234 fixes thing")
        assert not any("1234" in t for t in tokens)

    def test_strips_hash_issue_refs(self) -> None:
        tokens = tokenize("Fixes #123 the layer")
        assert "123" not in tokens

    def test_strips_signed_off_by(self) -> None:
        tokens = tokenize(
            "decision body\n\nSigned-off-by: Sam <sam@example.com>"
        )
        # "signed", "off" stripped via the line regex
        assert "signed" not in tokens

    def test_strips_conventional_commit_types(self) -> None:
        tokens = tokenize("feat: introduce repository pattern")
        # "feat" prefix line gets stripped — body remains
        assert "feat" not in tokens
        assert "introduce" in tokens
        assert "repository" in tokens


class TestOverlap:
    def test_overlap_count_matches_distinct_tokens(self) -> None:
        rationale = "Adopt repository pattern to decouple storage"
        pool = "We need to refactor storage into a repository abstraction"
        # Shared: repository, storage (and possibly others)
        assert overlap_count(rationale, pool_text=pool) >= 2

    def test_passes_l2_threshold(self) -> None:
        rationale = "Adopt repository pattern to decouple storage layer"
        pool = "We refactor storage into a repository abstraction layer"
        passed, count = passes_l2(rationale, pool_text=pool, min_overlap=3)
        assert passed is True
        assert count >= 3

    def test_fails_l2_below_threshold(self) -> None:
        rationale = "Quantum entanglement of meaningless prose"
        pool = "A diff that mentions buttons and CSS"
        passed, count = passes_l2(rationale, pool_text=pool, min_overlap=3)
        assert passed is False
        assert count < 3


class TestPolarity:
    def test_passes_when_title_term_in_pool(self) -> None:
        passed, matched = passes_l3(
            title="Repository pattern adoption",
            decision="Adopt repository",
            pool_text="diff: introduce repository module",
            required=True,
        )
        assert passed is True
        assert "repository" in matched

    def test_fails_when_no_match(self) -> None:
        passed, matched = passes_l3(
            title="Cosmic ray shielding",
            decision="Shield neutrinos",
            pool_text="introduce repository pattern",
            required=True,
        )
        assert passed is False
        assert matched == []

    def test_disabled_via_required_false(self) -> None:
        passed, matched = passes_l3(
            title="Nothing", decision="Nothing",
            pool_text="totally unrelated",
            required=False,
        )
        assert passed is True
