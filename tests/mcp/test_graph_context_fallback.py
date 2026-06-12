"""dec-116 #3: GLYPH is imported lazily.

A missing ``glyph-kg`` install must degrade only the graph tool, not the whole
MCP server. These tests pin both halves of that contract: the GLYPH-backed
symbols are not pulled in at server import time, and the tool returns a clean
install hint instead of a ``ModuleNotFoundError`` stacktrace.
"""

from __future__ import annotations

import builtins

import pytest

from axon.mcp import server


def test_glyph_symbols_not_imported_at_server_top_level() -> None:
    """If these were top-level imports, a missing glyph would kill *every* MCP
    tool (the whole module fails to import), not just the graph one."""
    assert not hasattr(server, "GraphContextSource")
    assert not hasattr(server, "GlyphEmbedderAdapter")


async def test_get_graph_context_degrades_when_glyph_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With glyph (and thus ``graph_source``) unimportable, the tool returns a
    clean install hint rather than raising."""
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if name == "axon.context.graph_source" or name.split(".")[0] == "glyph":
            raise ModuleNotFoundError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    response = await server.get_graph_context(query="anything")
    assert "glyph-kg" in response.lower()
