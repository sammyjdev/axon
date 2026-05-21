"""High-level orchestration API.

:class:`Orchestrator` is the single seam between the MCP tools and the
LangGraph state machine. It owns the compiled graph and the SQLite checkpointer
and exposes plan / get-next / record / replan / resume operations keyed by
session id.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from praxis.checkpoint import DEFAULT_DB_PATH, open_saver
from praxis.graph import build_graph
from praxis.state import Subtask, TaskState


def new_session_id() -> str:
    return f"sess-{uuid.uuid4().hex[:12]}"


class Orchestrator:
    """Run and resume task-orchestration sessions backed by a SQLite checkpoint."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._saver = open_saver(db_path)
        self._graph = build_graph(self._saver)
        self._lock = threading.Lock()

    @staticmethod
    def _config(session_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": session_id}}

    def _invoke(self, session_id: str, graph_input: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            result = self._graph.invoke(graph_input, self._config(session_id))
        return cast(dict[str, Any], result)

    def plan_task(
        self,
        goal: str,
        subtasks: list[Subtask] | list[dict[str, Any]] | None = None,
        session_id: str | None = None,
    ) -> TaskState:
        """Create a new session and populate its subtask plan."""
        session_id = session_id or new_session_id()
        seed = TaskState(session_id=session_id, goal=goal)
        graph_input: dict[str, Any] = {
            **seed.to_dict(),
            "action": "plan",
            "payload": {
                "goal": goal,
                "subtasks": [
                    s.to_dict() if isinstance(s, Subtask) else s
                    for s in (subtasks or [])
                ],
            },
        }
        return TaskState.from_dict(self._invoke(session_id, graph_input))

    def get_next_subtask(self, session_id: str) -> Subtask | None:
        """Advance the session and return its next actionable subtask."""
        result = self._invoke(session_id, {"action": "get_next", "payload": {}})
        data = (result.get("output") or {}).get("subtask")
        return Subtask.from_dict(data) if data else None

    def record_result(
        self, session_id: str, subtask_id: str, success: bool, detail: str = ""
    ) -> dict[str, Any]:
        """Record a subtask outcome and return the resulting summary."""
        result = self._invoke(
            session_id,
            {
                "action": "record",
                "payload": {
                    "subtask_id": subtask_id,
                    "success": success,
                    "detail": detail,
                },
            },
        )
        return dict(result.get("output") or {})

    def replan(
        self,
        session_id: str,
        extra_subtasks: list[Subtask] | list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Reset failed subtasks and optionally inject remediation subtasks."""
        result = self._invoke(
            session_id,
            {
                "action": "replan",
                "payload": {
                    "subtasks": [
                        s.to_dict() if isinstance(s, Subtask) else s
                        for s in (extra_subtasks or [])
                    ]
                },
            },
        )
        return dict(result.get("output") or {})

    def get_state(self, session_id: str) -> TaskState | None:
        """Read the checkpointed state for a session without running a node."""
        snapshot = self._graph.get_state(self._config(session_id))
        values = snapshot.values or {}
        if "session_id" not in values:
            return None
        return TaskState.from_dict(values)

    def resume_session(self, session_id: str) -> TaskState | None:
        """Load a session from its last checkpoint (alias of :meth:`get_state`)."""
        return self.get_state(session_id)

    def close(self) -> None:
        try:
            self._saver.conn.close()
        except Exception:
            pass
