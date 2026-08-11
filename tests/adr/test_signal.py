"""Tests for axon.adr.signal — commit message signal detector (dec-110)."""

from __future__ import annotations

from axon.adr.signal import LessonSignal, Signal, SignalKind, detect, detect_lesson


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


class TestDetectLesson:
    def test_trailer_in_body_returns_lesson_signal(self) -> None:
        msg = "fix: retry the flaky client\n\nLesson: retries need jitter"
        sig = detect_lesson(msg)
        assert sig is not None
        assert isinstance(sig, LessonSignal)
        assert sig.title == "retries need jitter"

    def test_trailer_alone_in_subject_does_not_match(self) -> None:
        assert detect_lesson("Lesson: foo") is None

    def test_trailer_case_insensitive(self) -> None:
        msg = "x\n\nlesson: lower case trailer"
        sig = detect_lesson(msg)
        assert sig is not None
        assert sig.title == "lower case trailer"

    def test_multiple_lesson_trailers_returns_first(self) -> None:
        msg = "x\n\nLesson: first\nLesson: second"
        sig = detect_lesson(msg)
        assert sig is not None
        assert sig.title == "first"

    def test_empty_title_returns_none(self) -> None:
        assert detect_lesson("x\n\nLesson:") is None
        assert detect_lesson("x\n\nLesson: ") is None

    def test_no_trailer_returns_none(self) -> None:
        assert detect_lesson("fix: bug in login flow") is None

    def test_empty_message_returns_none(self) -> None:
        assert detect_lesson("") is None
        assert detect_lesson("\n\n") is None

    def test_does_not_match_adr_decision_trailer(self) -> None:
        msg = "x\n\nADR-Decision: migrate to repository pattern"
        assert detect_lesson(msg) is None


class TestLessonAndAdrSignalsCoexist:
    """A commit can carry both trailers - a design choice can also be a
    hard-won lesson. The two detectors are independent: neither's
    presence affects the other, and both may fire on the same commit.
    """

    def test_both_trailers_present_both_detected(self) -> None:
        msg = (
            "fix: swap the retry client\n\n"
            "ADR-Decision: introduce a RetryPolicy abstraction\n"
            "Lesson: retries without jitter thundering-herd the upstream"
        )
        adr_sig = detect(msg)
        lesson_sig = detect_lesson(msg)

        assert adr_sig is not None
        assert adr_sig.kind == SignalKind.TRAILER
        assert adr_sig.title == "introduce a RetryPolicy abstraction"

        assert lesson_sig is not None
        assert lesson_sig.title == (
            "retries without jitter thundering-herd the upstream"
        )

    def test_lesson_only_leaves_adr_detect_none(self) -> None:
        msg = "fix: retry the flaky client\n\nLesson: retries need jitter"
        assert detect(msg) is None
        assert detect_lesson(msg) is not None
