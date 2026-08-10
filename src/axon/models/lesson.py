"""Lesson record and creation models."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

LessonKind = Literal["agent-error", "craft-lesson"]


class LessonCreate(BaseModel):
    kind: LessonKind
    triggers: list[str] = Field(min_length=1)
    mistake: str
    tell: str
    fix: str
    source: str


class LessonRecord(LessonCreate):
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    embedding: list[float] | None = None
