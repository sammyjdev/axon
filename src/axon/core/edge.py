"""The Edge domain model — a typed relationship between graph nodes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

EdgeType = Literal[
    "touches", "calls", "imports", "supersedes", "discussed_in", "committed_as"
]


class Edge(BaseModel):
    """A directed, typed edge between two node ids."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    target_id: str
    type: EdgeType
    payload: dict[str, Any] | None = None
