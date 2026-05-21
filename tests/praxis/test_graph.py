"""Task 9 — LangGraph nodes: plan -> get_next -> record -> next subtask."""

from __future__ import annotations

from pathlib import Path

from praxis.orchestrator import Orchestrator
from praxis.state import SubtaskStatus, TaskStatus


def test_plan_get_next_record_flow(tmp_path: Path) -> None:
    orchestrator = Orchestrator(str(tmp_path / "graph.sqlite"))
    try:
        state = orchestrator.plan_task(
            "demo goal",
            [
                {"id": "1", "title": "First"},
                {"id": "2", "title": "Second", "depends_on": ["1"]},
            ],
        )
        session_id = state.session_id
        assert state.status is TaskStatus.RUNNING
        assert len(state.subtasks) == 2

        first = orchestrator.get_next_subtask(session_id)
        assert first is not None and first.id == "1"

        # Subtask 2 is gated on subtask 1, so nothing else is available yet.
        outcome = orchestrator.record_result(session_id, "1", success=True, detail="done")
        assert outcome["outcome"] == "success"

        second = orchestrator.get_next_subtask(session_id)
        assert second is not None and second.id == "2"

        orchestrator.record_result(session_id, "2", success=True, detail="done")
        final = orchestrator.get_state(session_id)
        assert final is not None
        assert final.status is TaskStatus.DONE
        assert final.is_complete()
        assert len(final.history) == 2
    finally:
        orchestrator.close()


def test_replan_resets_failed_subtask(tmp_path: Path) -> None:
    orchestrator = Orchestrator(str(tmp_path / "replan.sqlite"))
    try:
        state = orchestrator.plan_task("demo", [{"id": "1", "title": "Flaky"}])
        session_id = state.session_id

        orchestrator.get_next_subtask(session_id)
        orchestrator.record_result(session_id, "1", success=False, detail="boom")

        failed = orchestrator.get_state(session_id)
        assert failed is not None
        failed_subtask = failed.find("1")
        assert failed_subtask is not None
        assert failed_subtask.status is SubtaskStatus.FAILED

        replanned = orchestrator.replan(session_id)
        assert "1" in replanned["reset"]

        retried = orchestrator.get_next_subtask(session_id)
        assert retried is not None and retried.id == "1"
    finally:
        orchestrator.close()
