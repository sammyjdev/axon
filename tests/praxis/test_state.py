"""Task 6 — TaskState / Subtask / History serialize and deserialize."""

from __future__ import annotations

from praxis.state import (
    History,
    HistoryEntry,
    Subtask,
    SubtaskStatus,
    TaskState,
    TaskStatus,
)


def _sample_state() -> TaskState:
    state = TaskState(session_id="sess-1", goal="ship sprint 1")
    state.subtasks = [
        Subtask(id="1", title="design"),
        Subtask(id="2", title="build", depends_on=["1"]),
        Subtask(id="3", title="verify", depends_on=["2"]),
    ]
    state.history.record("1", "success", "designed")
    return state


def test_subtask_roundtrip() -> None:
    subtask = Subtask(
        id="7",
        title="x",
        description="d",
        status=SubtaskStatus.IN_PROGRESS,
        depends_on=["1", "2"],
        attempts=3,
        result="ok",
    )
    assert Subtask.from_dict(subtask.to_dict()) == subtask


def test_history_entry_roundtrip() -> None:
    entry = HistoryEntry(subtask_id="9", outcome="success", detail="done")
    assert HistoryEntry.from_dict(entry.to_dict()) == entry


def test_history_roundtrip() -> None:
    history = History()
    history.record("1", "success", "a")
    history.record("2", "failure", "b")
    restored = History.from_dict(history.to_dict())
    assert restored == history
    assert len(restored) == 2


def test_taskstate_roundtrip_dict() -> None:
    state = _sample_state()
    assert TaskState.from_dict(state.to_dict()) == state


def test_taskstate_roundtrip_json() -> None:
    state = _sample_state()
    assert TaskState.from_json(state.to_json()) == state


def test_next_subtask_respects_dependencies() -> None:
    state = _sample_state()
    first = state.next_subtask()
    assert first is not None and first.id == "1"

    done = state.find("1")
    assert done is not None
    done.status = SubtaskStatus.DONE

    second = state.next_subtask()
    assert second is not None and second.id == "2"


def test_current_subtask_prefers_in_progress() -> None:
    state = _sample_state()
    target = state.find("2")
    assert target is not None
    target.status = SubtaskStatus.IN_PROGRESS

    current = state.current_subtask()
    assert current is not None and current.id == "2"


def test_progress_and_completion() -> None:
    state = _sample_state()
    assert state.progress() == (0, 3)
    assert not state.is_complete()
    assert state.status is TaskStatus.PLANNING

    for subtask in state.subtasks:
        subtask.status = SubtaskStatus.DONE

    assert state.progress() == (3, 3)
    assert state.is_complete()
