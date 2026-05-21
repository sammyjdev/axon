"""Task 10 — kill mid-task, restart, resume continues from the checkpoint."""

from __future__ import annotations

from pathlib import Path

from praxis.orchestrator import Orchestrator
from praxis.state import SubtaskStatus


def test_resume_continues_from_checkpoint(tmp_path: Path) -> None:
    db = str(tmp_path / "resume.sqlite")

    # Process 1: plan, hand out subtask 1, then "crash".
    first_run = Orchestrator(db)
    state = first_run.plan_task(
        "migrate",
        [
            {"id": "1", "title": "A"},
            {"id": "2", "title": "B", "depends_on": ["1"]},
        ],
    )
    session_id = state.session_id
    handed_out = first_run.get_next_subtask(session_id)
    assert handed_out is not None and handed_out.id == "1"
    first_run.close()
    del first_run

    # Process 2: a brand-new orchestrator on the same SQLite file.
    second_run = Orchestrator(db)
    try:
        resumed = second_run.resume_session(session_id)
        assert resumed is not None

        current = resumed.current_subtask()
        assert current is not None
        assert current.id == "1"
        assert current.status is SubtaskStatus.IN_PROGRESS

        # The flow continues from exactly where it stopped.
        second_run.record_result(session_id, "1", success=True, detail="after restart")
        nxt = second_run.get_next_subtask(session_id)
        assert nxt is not None and nxt.id == "2"
    finally:
        second_run.close()


def test_resume_unknown_session_returns_none(tmp_path: Path) -> None:
    orchestrator = Orchestrator(str(tmp_path / "resume-unknown.sqlite"))
    try:
        assert orchestrator.resume_session("does-not-exist") is None
    finally:
        orchestrator.close()
