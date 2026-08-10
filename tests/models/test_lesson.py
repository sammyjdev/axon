"""Tests for Lesson models (LessonCreate, LessonRecord)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from axon.models.lesson import LessonCreate, LessonKind, LessonRecord


def test_lesson_kind_valid_values() -> None:
    valid_kinds: list[LessonKind] = ["agent-error", "craft-lesson"]
    for kind in valid_kinds:
        lesson = LessonCreate(
            kind=kind,
            triggers=["git commit"],
            mistake="Failed to check git status",
            tell="Working tree clean check missing",
            fix="Run git status before commit",
            source="test_source",
        )
        assert lesson.kind == kind


@pytest.mark.parametrize(
    "invalid_kind",
    [
        "user-error",
        "craft_lesson",
        "AGENT-ERROR",
        "random-string",
        "",
        123,
    ],
)
def test_kind_rejects_anything_outside_allowed_literals(invalid_kind: str) -> None:
    with pytest.raises(ValidationError):
        LessonCreate(
            kind=invalid_kind,  # type: ignore[arg-type]
            triggers=["git commit"],
            mistake="Failed to check git status",
            tell="Working tree clean check missing",
            fix="Run git status before commit",
            source="test_source",
        )


def test_triggers_cannot_be_empty_in_lesson_create() -> None:
    with pytest.raises(ValidationError):
        LessonCreate(
            kind="agent-error",
            triggers=[],
            mistake="Failed to check git status",
            tell="Working tree clean check missing",
            fix="Run git status before commit",
            source="test_source",
        )


def test_triggers_cannot_be_empty_in_lesson_record() -> None:
    with pytest.raises(ValidationError):
        LessonRecord(
            kind="craft-lesson",
            triggers=[],
            mistake="Failed to check git status",
            tell="Working tree clean check missing",
            fix="Run git status before commit",
            source="test_source",
        )


def test_lesson_create_valid() -> None:
    lesson = LessonCreate(
        kind="agent-error",
        triggers=["trigger1", "trigger2"],
        mistake="A mistake",
        tell="A tell",
        fix="A fix",
        source="system",
    )
    assert lesson.kind == "agent-error"
    assert lesson.triggers == ["trigger1", "trigger2"]
    assert lesson.mistake == "A mistake"
    assert lesson.tell == "A tell"
    assert lesson.fix == "A fix"
    assert lesson.source == "system"


def test_lesson_record_defaults() -> None:
    record = LessonRecord(
        kind="craft-lesson",
        triggers=["trigger1"],
        mistake="A mistake",
        tell="A tell",
        fix="A fix",
        source="system",
    )
    assert isinstance(record.id, UUID)
    assert isinstance(record.created_at, datetime)
    assert record.created_at.tzinfo == UTC
    assert record.embedding is None


def test_lesson_record_explicit_fields() -> None:
    custom_id = uuid4()
    custom_dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    custom_embedding = [0.1, 0.2, 0.3]

    record = LessonRecord(
        id=custom_id,
        created_at=custom_dt,
        embedding=custom_embedding,
        kind="agent-error",
        triggers=["trigger1"],
        mistake="A mistake",
        tell="A tell",
        fix="A fix",
        source="system",
    )
    assert record.id == custom_id
    assert record.created_at == custom_dt
    assert record.embedding == [0.1, 0.2, 0.3]
