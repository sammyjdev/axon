"""Tasks 3, 4, 5 + Sprint 1 done-criterion — the MCP server end to end."""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

import praxis.server as server

EXPECTED_TOOLS = {
    "praxis_health",
    "plan_task",
    "get_next_subtask",
    "record_result",
    "replan",
    "praxis_resume_session",
    "get_session_status",
}


@contextlib.asynccontextmanager
async def connected_session() -> AsyncIterator[tuple[ClientSession, Any]]:
    """Run the Praxis MCP server over in-memory streams and connect a client."""
    low_level = server.mcp._mcp_server
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(
                lambda: low_level.run(
                    server_read,
                    server_write,
                    low_level.create_initialization_options(),
                    raise_exceptions=True,
                )
            )
            try:
                async with ClientSession(client_read, client_write) as session:
                    init_result = await session.initialize()
                    yield session, init_result
            finally:
                task_group.cancel_scope.cancel()


def _payload(result: Any) -> dict[str, Any]:
    assert not result.isError, result
    if result.structuredContent:
        return dict(result.structuredContent)
    return json.loads(result.content[0].text)


async def test_server_responds_to_initialize(praxis_env: Any) -> None:
    async with connected_session() as (_session, init):
        assert init.serverInfo.name == "praxis"
        assert init.capabilities.tools is not None


async def test_lists_seven_tools_with_valid_schema(praxis_env: Any) -> None:
    async with connected_session() as (session, _init):
        tools = (await session.list_tools()).tools
        assert len(tools) == 7
        assert {t.name for t in tools} == EXPECTED_TOOLS
        for tool in tools:
            schema = tool.inputSchema
            assert isinstance(schema, dict)
            assert schema.get("type") == "object"
            assert isinstance(schema.get("properties", {}), dict)
            assert tool.description


async def test_record_result_schema_marks_required_params(praxis_env: Any) -> None:
    async with connected_session() as (session, _init):
        tools = {t.name: t for t in (await session.list_tools()).tools}
        schema = tools["record_result"].inputSchema
        assert {"session_id", "subtask_id", "success"} <= set(schema.get("required", []))
        assert schema["properties"]["success"]["type"] == "boolean"


async def test_health_tool_reports_status_version_uptime(praxis_env: Any) -> None:
    async with connected_session() as (session, _init):
        health = _payload(await session.call_tool("praxis_health", {}))
        assert health["status"] == "ok"
        assert health["version"]
        assert health["uptime"] >= 0


async def test_mock_flow_end_to_end_survives_restart(
    praxis_env: Any, spec_text: str
) -> None:
    # Process 1: plan from the Markdown spec, take subtask 1, record success.
    async with connected_session() as (session, _init):
        planned = _payload(await session.call_tool("plan_task", {"spec": spec_text}))
        session_id = planned["session_id"]
        assert len(planned["subtasks"]) == 5

        first = _payload(
            await session.call_tool("get_next_subtask", {"session_id": session_id})
        )
        assert first["subtask"]["id"] == "1"

        recorded = _payload(
            await session.call_tool(
                "record_result",
                {
                    "session_id": session_id,
                    "subtask_id": "1",
                    "success": True,
                    "detail": "audited",
                },
            )
        )
        assert recorded["outcome"] == "success"

    # Restart: a brand-new orchestrator reading the same SQLite checkpoint.
    praxis_env.boot()

    async with connected_session() as (session, _init):
        resumed = _payload(
            await session.call_tool(
                "praxis_resume_session", {"session_id": session_id}
            )
        )
        assert resumed["found"] is True
        assert resumed["progress"] == {"done": 1, "total": 5}

        nxt = _payload(
            await session.call_tool("get_next_subtask", {"session_id": session_id})
        )
        assert nxt["subtask"]["id"] == "2"
