"""The Symbol domain model — a code entity in the knowledge graph."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SymbolType = Literal["class", "method", "function", "interface", "enum"]
Language = Literal["python", "java"]


class Symbol(BaseModel):
    """A resolved code symbol (class, method, function, ...).

    ``id`` is the fully qualified name, e.g. ``pkg.module.Class.method``.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    type: SymbolType
    file: Path
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    language: Language

    @model_validator(mode="after")
    def _check_span(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError(
                f"end_line ({self.end_line}) < start_line ({self.start_line})"
            )
        return self
