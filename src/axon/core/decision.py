"""The Decision domain model — AXON's unit of captured context."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator

from axon.exceptions import ValidationError

Agent = Literal["claude-code", "codex", "cursor", "manual"]
Status = Literal["draft", "active", "superseded", "deprecated"]

_ID_RE = re.compile(r"^dec-\d{3,}$")
_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


class Decision(BaseModel):
    """A captured architectural / coding decision.

    Round-trips losslessly to a markdown document with a YAML frontmatter
    block via :meth:`to_markdown` and :meth:`from_markdown`.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    timestamp: datetime
    agent: Agent
    repo: str
    files: list[Path] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    summary: str = Field(max_length=80)
    validation_score: float = Field(default=0.0, ge=0.0, le=5.0)
    judged: bool = False
    git_hash: str | None = None
    linked_decisions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: Status = "draft"

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(rf"id must match ^dec-\d{{3,}}$, got {value!r}")
        return value

    @field_validator("timestamp")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @field_validator("tags")
    @classmethod
    def _kebab_tags(cls, value: list[str]) -> list[str]:
        for tag in value:
            if not _KEBAB_RE.match(tag):
                raise ValueError(f"tag must be lowercase kebab-case, got {tag!r}")
        return value

    def to_markdown(self) -> str:
        """Serialize to a markdown document with a YAML frontmatter block.

        The ``summary`` field becomes the document body; every other field
        goes into the frontmatter.
        """
        data = self.model_dump(mode="json")
        body = data.pop("summary")
        front = yaml.safe_dump(data, sort_keys=True, allow_unicode=True).strip()
        return f"---\n{front}\n---\n\n{body}\n"

    @classmethod
    def from_markdown(cls, text: str) -> Self:
        """Parse a markdown document produced by :meth:`to_markdown`."""
        match = _FRONTMATTER_RE.match(text)
        if match is None:
            raise ValidationError("missing YAML frontmatter block")
        data = yaml.safe_load(match.group(1)) or {}
        data["summary"] = match.group(2).strip()
        return cls(**data)
