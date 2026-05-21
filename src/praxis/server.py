"""Praxis MCP server.

Exposes the orchestration engine as seven MCP tools over the FastMCP runtime.
Run it with ``praxis-server`` (stdio transport).
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from praxis.health import health_report
from praxis.orchestrator import Orchestrator
from praxis.parser import parse_spec
from praxis.state import Subtask

DB_PATH = os.environ.get("PRAXIS_DB", ".praxis/praxis.sqlite")

mcp = FastMCP(
    "praxis",
    host=os.environ.get("PRAXIS_HOST", "127.0.0.1"),
    port=int(os.environ.get("PRAXIS_PORT", "8000")),
)

_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    """Return the process-wide orchestrator, creating it on first use."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(DB_PATH)
    return _orchestrator


@mcp.tool()
def praxis_health() -> dict[str, Any]:
    """Report server health: status, version, and uptime in seconds."""
    return health_report()


@mcp.tool()
def plan_task(goal: str = "", spec: str = "", session_id: str = "") -> dict[str, Any]:
    """Create an orchestration session.

    Provide a Markdown ``spec`` (parsed into subtasks) or a plain ``goal``.
    Returns the new session id and its planned subtasks.
    """
    subtasks: list[Subtask]
    if spec.strip():
        parsed = parse_spec(spec)
        goal = goal or parsed.goal or parsed.title
        subtasks = parsed.subtasks
    elif goal.strip():
        subtasks = [Subtask(id="1", title=goal.strip())]
    else:
        return {"error": "provide a `goal` or a Markdown `spec`"}

    state = get_orchestrator().plan_task(
        goal=goal, subtasks=subtasks, session_id=session_id or None
    )
    return {
        "session_id": state.session_id,
        "goal": state.goal,
        "status": state.status.value,
        "subtasks": [s.to_dict() for s in state.subtasks],
    }


@mcp.tool()
def get_next_subtask(session_id: str) -> dict[str, Any]:
    """Return the next actionable subtask for a session, or ``done=true``."""
    nxt = get_orchestrator().get_next_subtask(session_id)
    if nxt is None:
        return {"session_id": session_id, "subtask": None, "done": True}
    return {"session_id": session_id, "subtask": nxt.to_dict(), "done": False}


@mcp.tool()
def record_result(
    session_id: str, subtask_id: str, success: bool, detail: str = ""
) -> dict[str, Any]:
    """Record the outcome (success or failure) of a subtask."""
    return get_orchestrator().record_result(session_id, subtask_id, success, detail)


@mcp.tool()
def replan(session_id: str) -> dict[str, Any]:
    """Reset failed subtasks to pending so the session can retry them."""
    return get_orchestrator().replan(session_id)


@mcp.tool()
def praxis_resume_session(session_id: str) -> dict[str, Any]:
    """Resume a session from its last checkpoint and report what to do next."""
    state = get_orchestrator().resume_session(session_id)
    if state is None:
        return {"session_id": session_id, "found": False}
    nxt = state.current_subtask()
    done, total = state.progress()
    return {
        "session_id": session_id,
        "found": True,
        "status": state.status.value,
        "progress": {"done": done, "total": total},
        "next_subtask": nxt.to_dict() if nxt else None,
    }


@mcp.tool()
def get_session_status(session_id: str) -> dict[str, Any]:
    """Return the full current state of an orchestration session."""
    state = get_orchestrator().get_state(session_id)
    if state is None:
        return {"session_id": session_id, "found": False}
    done, total = state.progress()
    return {
        "session_id": session_id,
        "found": True,
        "goal": state.goal,
        "status": state.status.value,
        "progress": {"done": done, "total": total},
        "subtasks": [s.to_dict() for s in state.subtasks],
        "history": state.history.to_dict()["entries"],
    }


def main() -> None:
    """Entry point for the ``praxis-server`` console script.

    Defaults to stdio transport for local MCP clients. Set ``PRAXIS_TRANSPORT``
    to ``streamable-http`` or ``sse`` to run as a long-lived networked service
    (used by the Docker image).
    """
    transport = os.environ.get("PRAXIS_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run()
    elif transport == "sse":
        mcp.run(transport="sse")
    elif transport == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        raise ValueError(f"unknown PRAXIS_TRANSPORT: {transport!r}")


if __name__ == "__main__":
    main()
