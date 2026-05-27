"""Tests for axon.adr.signal — commit message signal detector (dec-110)."""

from __future__ import annotations

from axon.adr.signal import Signal, SignalKind, detect


class TestDetectSubjectPrefix:
    def test_arch_prefix_returns_signal(self) -> None:
        sig = detect("arch: migrate auth to JWT")
        assert sig is not None
        assert sig.kind == SignalKind.SUBJECT_PREFIX
        assert sig.title == "migrate auth to JWT"

    def test_decision_prefix_returns_signal(self) -> None:
        sig = detect("decision: drop Neo4j")
        assert sig is not None
        assert sig.kind == SignalKind.SUBJECT_PREFIX
        assert sig.title == "drop Neo4j"

    def test_no_prefix_returns_none(self) -> None:
        assert detect("fix: bug in login flow") is None
        assert detect("feat: add new feature") is None
        assert detect("refactor: extract helper") is None

    def test_prefix_must_be_at_start(self) -> None:
        assert detect("WIP arch: thing") is None
        assert detect("(arch): thing") is None

    def test_prefix_is_case_sensitive(self) -> None:
        # Conventional Commits uses lowercase; enforce same
        assert detect("ARCH: thing") is None
        assert detect("Arch: thing") is None

    def test_empty_subject_after_prefix_returns_none(self) -> None:
        assert detect("arch:") is None
        assert detect("arch: ") is None

    def test_prefix_with_scope_works(self) -> None:
        # Conventional Commits scope syntax: arch(auth):
        sig = detect("arch(auth): replace session middleware")
        assert sig is not None
        assert sig.kind == SignalKind.SUBJECT_PREFIX
        assert sig.title == "replace session middleware"

    def test_breaking_change_marker_works(self) -> None:
        sig = detect("arch!: rewrite storage layer")
        assert sig is not None
        assert sig.title == "rewrite storage layer"


class TestDetectTrailer:
    def test_trailer_in_body_returns_signal(self) -> None:
        msg = "fix: typo\n\nADR-Decision: migrate to repository pattern"
        sig = detect(msg)
        assert sig is not None
        assert sig.kind == SignalKind.TRAILER
        assert sig.title == "migrate to repository pattern"

    def test_trailer_alone_in_subject_does_not_match(self) -> None:
        # Trailer must be in body (after blank line), not subject
        assert detect("ADR-Decision: foo") is None

    def test_trailer_case_insensitive(self) -> None:
        msg = "x\n\nadr-decision: lower"
        sig = detect(msg)
        assert sig is not None
        msg2 = "y\n\nADR-DECISION: upper"
        sig2 = detect(msg2)
        assert sig2 is not None

    def test_multiple_trailers_returns_first(self) -> None:
        msg = "x\n\nADR-Decision: first\nADR-Decision: second"
        sig = detect(msg)
        assert sig is not None
        assert sig.title == "first"


class TestPrecedence:
    def test_subject_prefix_wins_over_trailer(self) -> None:
        msg = "arch: from subject\n\nADR-Decision: from trailer"
        sig = detect(msg)
        assert sig is not None
        assert sig.kind == SignalKind.SUBJECT_PREFIX
        assert sig.title == "from subject"


class TestEdgeCases:
    def test_empty_message_returns_none(self) -> None:
        assert detect("") is None
        assert detect("\n\n") is None

    def test_only_whitespace_returns_none(self) -> None:
        assert detect("   ") is None

    def test_multiline_subject_handled(self) -> None:
        # Subject is only the first line
        sig = detect("arch: rebuild\n\ndetails here")
        assert sig is not None
        assert sig.title == "rebuild"


class TestSignal:
    def test_signal_is_dataclass_with_kind_and_title(self) -> None:
        sig = Signal(kind=SignalKind.SUBJECT_PREFIX, title="foo")
        assert sig.kind == SignalKind.SUBJECT_PREFIX
        assert sig.title == "foo"
