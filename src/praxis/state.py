"""Serializable state schema for the Praxis task graph.

Three dataclasses model an orchestration run:

* :class:`Subtask` — a single unit of work.
* :class:`History` — an append-only log of :class:`HistoryEntry` records.
* :class:`TaskState` — the full graph state (the LangGraph channel payload).

Every type round-trips through ``to_dict`` / ``from_dict`` so checkpoints stay
plain JSON and reload identically across process restarts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypedDict


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SubtaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStatus(StrEnum):
    PLANNING = "planning"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Subtask:
    """A single unit of work inside a :class:`TaskState`."""

    id: str
    title: str
    description: str = ""
    status: SubtaskStatus = SubtaskStatus.PENDING
    depends_on: list[str] = field(default_factory=list)
    attempts: int = 0
    result: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "depends_on": list(self.depends_on),
            "attempts": self.attempts,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Subtask:
        return cls(
            id=str(data["id"]),
            title=str(data["title"]),
            description=str(data.get("description", "")),
            status=SubtaskStatus(data.get("status", SubtaskStatus.PENDING.value)),
            depends_on=[str(d) for d in data.get("depends_on", [])],
            attempts=int(data.get("attempts", 0)),
            result=data.get("result"),
        )


@dataclass
class HistoryEntry:
    """One recorded outcome for a subtask."""

    subtask_id: str
    outcome: str
    detail: str = ""
    timestamp: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "outcome": self.outcome,
            "detail": self.detail,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HistoryEntry:
        return cls(
            subtask_id=str(data["subtask_id"]),
            outcome=str(data["outcome"]),
            detail=str(data.get("detail", "")),
            timestamp=str(data.get("timestamp") or _utc_now()),
        )


@dataclass
class History:
    """Append-only log of subtask outcomes."""

    entries: list[HistoryEntry] = field(default_factory=list)

    def record(self, subtask_id: str, outcome: str, detail: str = "") -> HistoryEntry:
        entry = HistoryEntry(subtask_id=subtask_id, outcome=outcome, detail=detail)
        self.entries.append(entry)
        return entry

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Any:
        return iter(self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [entry.to_dict() for entry in self.entries]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | list[Any]) -> History:
        raw = data.get("entries", []) if isinstance(data, Mapping) else data
        return cls(entries=[HistoryEntry.from_dict(entry) for entry in raw])


@dataclass
class TaskState:
    """Full state of an orchestration run — the LangGraph channel payload."""

    session_id: str
    goal: str
    subtasks: list[Subtask] = field(default_factory=list)
    cursor: int = 0
    history: History = field(default_factory=History)
    status: TaskStatus = TaskStatus.PLANNING
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    def touch(self) -> None:
        self.updated_at = _utc_now()

    def find(self, subtask_id: str) -> Subtask | None:
        for subtask in self.subtasks:
            if subtask.id == subtask_id:
                return subtask
        return None

    def next_subtask(self) -> Subtask | None:
        """Return the first pending subtask whose dependencies are all done."""
        done = {s.id for s in self.subtasks if s.status is SubtaskStatus.DONE}
        for subtask in self.subtasks:
            if subtask.status is not SubtaskStatus.PENDING:
                continue
            if all(dep in done for dep in subtask.depends_on):
                return subtask
        return None

    def current_subtask(self) -> Subtask | None:
        """Return the subtask to work on now.

        An in-progress subtask (handed out but not yet recorded) takes
        precedence — that is what a resumed session should continue with.
        """
        for subtask in self.subtasks:
            if subtask.status is SubtaskStatus.IN_PROGRESS:
                return subtask
        return self.next_subtask()

    def is_complete(self) -> bool:
        terminal = {SubtaskStatus.DONE, SubtaskStatus.SKIPPED}
        return bool(self.subtasks) and all(s.status in terminal for s in self.subtasks)

    def progress(self) -> tuple[int, int]:
        done = sum(1 for s in self.subtasks if s.status is SubtaskStatus.DONE)
        return done, len(self.subtasks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "subtasks": [s.to_dict() for s in self.subtasks],
            "cursor": self.cursor,
            "history": self.history.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskState:
        return cls(
            session_id=str(data["session_id"]),
            goal=str(data.get("goal", "")),
            subtasks=[Subtask.from_dict(s) for s in data.get("subtasks", [])],
            cursor=int(data.get("cursor", 0)),
            history=History.from_dict(data.get("history", {})),
            status=TaskStatus(data.get("status", TaskStatus.PLANNING.value)),
            created_at=str(data.get("created_at") or _utc_now()),
            updated_at=str(data.get("updated_at") or _utc_now()),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> TaskState:
        return cls.from_dict(json.loads(payload))


class GraphState(TypedDict, total=False):
    """Channel schema for the LangGraph orchestration graph.

    The persistent channels mirror :meth:`TaskState.to_dict`; the control
    channels (``action`` / ``payload`` / ``output``) carry per-invocation
    input and result data.
    """

    session_id: str
    goal: str
    subtasks: list[dict[str, Any]]
    cursor: int
    history: dict[str, Any]
    status: str
    created_at: str
    updated_at: str
    action: str
    payload: dict[str, Any]
    output: dict[str, Any]
