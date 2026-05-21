"""Task 7 — SqliteSaver checkpoint survives a process restart."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from praxis.checkpoint import open_saver
from praxis.graph import build_graph
from praxis.state import TaskState


def _plan_input(session_id: str) -> dict[str, Any]:
    seed = TaskState(session_id=session_id, goal="demo")
    return {
        **seed.to_dict(),
        "action": "plan",
        "payload": {
            "goal": "demo",
            "subtasks": [
                {"id": "1", "title": "A"},
                {"id": "2", "title": "B", "depends_on": ["1"]},
            ],
        },
    }


def test_checkpoint_persists_across_restart(tmp_path: Path) -> None:
    db = tmp_path / "cp.sqlite"
    config = {"configurable": {"thread_id": "sess-cp"}}

    saver = open_saver(db)
    graph = build_graph(saver)
    out = graph.invoke(_plan_input("sess-cp"), config)
    before = TaskState.from_dict(out)
    saver.conn.close()

    # Fresh process: a new saver + graph reading the same SQLite file.
    restarted_saver = open_saver(db)
    restarted_graph = build_graph(restarted_saver)
    snapshot = restarted_graph.get_state(config)
    after = TaskState.from_dict(snapshot.values)
    restarted_saver.conn.close()

    assert after == before
    assert [s.id for s in after.subtasks] == ["1", "2"]
    assert after.session_id == "sess-cp"
