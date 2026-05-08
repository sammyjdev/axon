from __future__ import annotations

from pathlib import Path

from prometheus.registry import PluginManifest, ToolDescriptor


def test_plugin_manifest_normalizes_optional_collections() -> None:
    manifest = PluginManifest(
        plugin_id="acme.search",
        name="Acme Search",
        version="1.0.0",
        manifest_path=Path("/tmp/plugin.json"),
        contexts=("knowledge", "saas", "knowledge"),
        capability_tags=("search", "offline-first", "search"),
        tool_descriptors=(Path("tools/query.json"), Path("tools/query.json")),
    )

    assert manifest.contexts == ("knowledge", "saas")
    assert manifest.capability_tags == ("offline-first", "search")
    assert manifest.tool_descriptors == (Path("tools/query.json"),)


def test_tool_descriptor_normalizes_optional_collections() -> None:
    descriptor = ToolDescriptor(
        tool_id="acme.search.query",
        plugin_id="acme.search",
        name="Query",
        description="Runs a query",
        descriptor_path=Path("/tmp/tools/query.json"),
        contexts=("knowledge", "saas", "knowledge"),
        packs=("core", "ops", "core"),
        capability_tags=("search", "offline-first", "search"),
    )

    assert descriptor.contexts == ("knowledge", "saas")
    assert descriptor.packs == ("core", "ops")
    assert descriptor.capability_tags == ("offline-first", "search")
