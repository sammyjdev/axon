"""LangGraph node functions for the Praxis orchestration graph.

Each node receives the graph state as a plain ``dict`` (so checkpoints stay
JSON-clean), rebuilds a typed :class:`TaskState`, mutates it, and returns the
full serialized state plus an ``output`` payload the caller reads back.
"""

from __future__ import annotations

from typing import Any, cast

from praxis.state import GraphState, Subtask, SubtaskStatus, TaskState, TaskStatus


def _load(state: GraphState) -> TaskState:
    return TaskState.from_dict(state)


def _emit(state: TaskState, output: dict[str, Any]) -> GraphState:
    state.touch()
    payload: dict[str, Any] = state.to_dict()
    payload["output"] = output
    payload["action"] = ""
    payload["payload"] = {}
    return cast(GraphState, payload)


def _coerce_subtask(item: Any, fallback_id: str) -> Subtask:
    if isinstance(item, Subtask):
        return item
    if isinstance(item, dict):
        data = dict(item)
        data.setdefault("id", fallback_id)
        data.setdefault("title", f"Subtask {fallback_id}")
        return Subtask.from_dict(data)
    return Subtask(id=fallback_id, title=str(item))


def plan_node(state: GraphState) -> GraphState:
    """Build the subtask list for a session from the plan payload."""
    payload = state.get("payload") or {}
    task_state = _load(state)
    task_state.goal = str(payload.get("goal") or task_state.goal or "")
    raw = payload.get("subtasks") or []
    task_state.subtasks = [
        _coerce_subtask(item, str(idx)) for idx, item in enumerate(raw, start=1)
    ]
    task_state.cursor = 0
    task_state.status = TaskStatus.RUNNING if task_state.subtasks else TaskStatus.PLANNING
    return _emit(
        task_state,
        {
            "action": "plan",
            "session_id": task_state.session_id,
            "planned": len(task_state.subtasks),
            "subtasks": [s.to_dict() for s in task_state.subtasks],
        },
    )


def get_next_node(state: GraphState) -> GraphState:
    """Hand out the next actionable subtask and mark it in progress."""
    task_state = _load(state)
    nxt = task_state.next_subtask()
    if nxt is not None:
        nxt.status = SubtaskStatus.IN_PROGRESS
        task_state.cursor = task_state.subtasks.index(nxt)
        output = {"action": "get_next", "subtask": nxt.to_dict(), "done": False}
    else:
        if task_state.is_complete():
            task_state.status = TaskStatus.DONE
        done, total = task_state.progress()
        output = {
            "action": "get_next",
            "subtask": None,
            "done": task_state.is_complete(),
            "progress": {"done": done, "total": total},
        }
    return _emit(task_state, output)


def record_node(state: GraphState) -> GraphState:
    """Record the outcome of a subtask and update overall status."""
    payload = state.get("payload") or {}
    task_state = _load(state)
    subtask_id = str(payload.get("subtask_id", ""))
    success = bool(payload.get("success", False))
    detail = str(payload.get("detail", ""))

    subtask = task_state.find(subtask_id)
    if subtask is None:
        return _emit(
            task_state,
            {"action": "record", "error": f"unknown subtask '{subtask_id}'"},
        )

    subtask.attempts += 1
    subtask.result = detail
    if success:
        subtask.status = SubtaskStatus.DONE
        task_state.history.record(subtask_id, "success", detail)
    else:
        subtask.status = SubtaskStatus.FAILED
        task_state.history.record(subtask_id, "failure", detail)

    if task_state.is_complete():
        task_state.status = TaskStatus.DONE
    else:
        task_state.status = TaskStatus.RUNNING

    done, total = task_state.progress()
    return _emit(
        task_state,
        {
            "action": "record",
            "subtask_id": subtask_id,
            "outcome": "success" if success else "failure",
            "progress": {"done": done, "total": total},
            "complete": task_state.is_complete(),
        },
    )


def replan_node(state: GraphState) -> GraphState:
    """Reset failed subtasks to pending and append any remediation subtasks."""
    payload = state.get("payload") or {}
    task_state = _load(state)

    reset = []
    for subtask in task_state.subtasks:
        if subtask.status is SubtaskStatus.FAILED:
            subtask.status = SubtaskStatus.PENDING
            reset.append(subtask.id)

    added = []
    extra = payload.get("subtasks") or []
    base = len(task_state.subtasks)
    for idx, item in enumerate(extra, start=base + 1):
        subtask = _coerce_subtask(item, f"r{idx}")
        task_state.subtasks.append(subtask)
        added.append(subtask.id)

    if reset or added:
        task_state.status = TaskStatus.RUNNING
    return _emit(task_state, {"action": "replan", "reset": reset, "added": added})
