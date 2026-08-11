"""The MCP tool contract is a public interface; changes to it must be deliberate.

Nothing in the repo detected contract drift before this. Finding out whether the
mcp 2.0 upgrade changed what clients see required writing a throwaway probe by
hand. A tool's name, description and schemas are what Claude Code and any other
client consume - a silent change there alters agent behaviour with no test
failing anywhere.

These tests pin the shape, not the content: they do not assert on specific
descriptions (which are edited often and legitimately), but on the invariants a
client depends on - the tool set, and every tool exposing a well-formed schema
under the wire alias.
"""
from __future__ import annotations

import pytest

from axon.mcp import server

# Every tool the server exposes. Adding or removing one is a client-visible
# change: update this list in the same commit, so the diff shows it.
EXPECTED_TOOLS = {
    "ask",
    "axon_capture",
    "axon_capture_event",
    "axon_export_now",
    "axon_get_context",
    "axon_handoff",
    "axon_health",
    "axon_mark_done",
    "axon_record_lesson",
    "axon_record_outcome",
    "axon_search",
    "axon_session_end",
    "axon_session_start",
    "axon_validation_stats",
    "get_adrs",
    "get_dependencies",
    "get_graph_context",
    "get_graph_neighbors",
    "get_graph_path",
    "get_session_memory",
    "restore_context",
    "save_adr",
    "search_code",
}


@pytest.fixture
async def tools():
    return await server.mcp.list_tools()


async def test_exposed_tool_set_is_unchanged(tools) -> None:
    names = {t.name for t in tools}
    added = names - EXPECTED_TOOLS
    removed = EXPECTED_TOOLS - names
    assert not added, f"new tools exposed without updating this list: {added}"
    assert not removed, f"tools removed from the MCP surface: {removed}"


async def test_every_tool_has_a_description(tools) -> None:
    """Descriptions are how the agent decides when to call a tool."""
    missing = [t.name for t in tools if not (t.description or "").strip()]
    assert not missing, f"tools with no description: {missing}"


async def test_every_tool_exposes_an_object_input_schema(tools) -> None:
    """Under the wire alias, not the Python field name.

    mcp 2.0 renamed the model fields to snake_case; `model_dump()` alone made all
    22 tools look changed when the serialised contract was in fact identical.
    Asserting on the aliased dump is what makes this test version-independent.
    """
    for tool in tools:
        wire = tool.model_dump(by_alias=True, exclude_none=True)
        schema = wire.get("inputSchema")
        assert schema is not None, f"{tool.name}: no inputSchema on the wire"
        assert schema.get("type") == "object", f"{tool.name}: inputSchema is not an object"
        assert "properties" in schema, f"{tool.name}: inputSchema has no properties"


async def test_ctx_taking_tools_declare_it_as_an_optional_string(tools) -> None:
    """`ctx` selects the restricted-context boundary - its shape is load-bearing."""
    by_name = {t.name: t for t in tools}
    for name in ("search_code", "ask"):
        props = by_name[name].model_dump(by_alias=True)["inputSchema"]["properties"]
        assert "ctx" in props, f"{name} no longer accepts ctx"
        assert "ctx" not in by_name[name].model_dump(by_alias=True)["inputSchema"].get(
            "required", []
        ), f"{name}: ctx became required"
