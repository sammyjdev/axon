from __future__ import annotations

import json
from pathlib import Path

import pytest

from prometheus.registry import discover_local_registry


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_discover_local_registry_loads_plugin_manifests_and_tool_descriptors(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    plugin_dir = root / "acme-search"
    _write_json(
        plugin_dir / "plugin.json",
        {
            "plugin_id": "acme.search",
            "name": "Acme Search",
            "version": "1.0.0",
            "description": "Local search integration",
            "enabled": True,
            "contexts": ["knowledge", "saas"],
            "capability_tags": ["search", "offline-first"],
            "tool_descriptors": ["tools/query.json", "tools/summarize.json"],
        },
    )
    _write_json(
        plugin_dir / "tools" / "query.json",
        {
            "tool_id": "acme.search.query",
            "plugin_id": "acme.search",
            "name": "Query Search",
            "description": "Runs a local knowledge query",
            "contexts": ["knowledge"],
            "packs": ["core"],
            "capability_tags": ["search"],
        },
    )
    _write_json(
        plugin_dir / "tools" / "summarize.json",
        {
            "tool_id": "acme.search.summarize",
            "plugin_id": "acme.search",
            "name": "Summarize Search Result",
            "description": "Summarizes local results",
            "contexts": ["knowledge", "saas"],
            "packs": ["core", "ops"],
            "capability_tags": ["summarization"],
        },
    )

    registry = discover_local_registry(root)

    assert [plugin.plugin_id for plugin in registry.plugins] == ["acme.search"]
    assert registry.plugins[0].contexts == ("knowledge", "saas")
    assert registry.plugins[0].capability_tags == ("offline-first", "search")
    assert [tool.tool_id for tool in registry.tools] == [
        "acme.search.query",
        "acme.search.summarize",
    ]
    assert [
        tool.tool_id for tool in registry.list_tools(ctx="knowledge", pack="core")
    ] == [
        "acme.search.query",
        "acme.search.summarize",
    ]
    assert [tool.tool_id for tool in registry.list_tools(ctx="saas", pack="ops")] == [
        "acme.search.summarize"
    ]


def test_discover_local_registry_returns_empty_registry_when_root_is_missing(
    tmp_path: Path,
) -> None:
    registry = discover_local_registry(tmp_path / "missing")

    assert registry.plugins == ()
    assert registry.tools == ()


def test_discover_local_registry_rejects_duplicate_plugin_ids(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    for slug in ("plugin-a", "plugin-b"):
        _write_json(
            root / slug / "plugin.json",
            {
                "plugin_id": "acme.search",
                "name": slug,
                "version": "1.0.0",
                "tool_descriptors": [],
            },
        )

    with pytest.raises(ValueError, match="plugin duplicado"):
        discover_local_registry(root)


def test_discover_local_registry_rejects_duplicate_tool_ids(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    for slug in ("plugin-a", "plugin-b"):
        _write_json(
            root / slug / "plugin.json",
            {
                "plugin_id": f"acme.{slug}",
                "name": slug,
                "version": "1.0.0",
                "tool_descriptors": ["tools/query.json"],
            },
        )
        _write_json(
            root / slug / "tools" / "query.json",
            {
                "tool_id": "shared.query",
                "plugin_id": f"acme.{slug}",
                "name": "Query",
                "description": "Runs a query",
            },
        )

    with pytest.raises(ValueError, match="tool duplicado"):
        discover_local_registry(root)


def test_discover_local_registry_rejects_invalid_context_in_plugin_manifest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    _write_json(
        root / "plugin-a" / "plugin.json",
        {
            "plugin_id": "acme.search",
            "name": "Acme Search",
            "version": "1.0.0",
            "contexts": ["client"],
            "tool_descriptors": [],
        },
    )

    with pytest.raises(ValueError, match="ctx inválido"):
        discover_local_registry(root)


def test_discover_local_registry_rejects_invalid_context_in_tool_descriptor(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    _write_json(
        root / "plugin-a" / "plugin.json",
        {
            "plugin_id": "acme.search",
            "name": "Acme Search",
            "version": "1.0.0",
            "tool_descriptors": ["tools/query.json"],
        },
    )
    _write_json(
        root / "plugin-a" / "tools" / "query.json",
        {
            "tool_id": "acme.search.query",
            "plugin_id": "acme.search",
            "name": "Query",
            "description": "Runs a query",
            "contexts": ["client"],
        },
    )

    with pytest.raises(ValueError, match="ctx inválido"):
        discover_local_registry(root)


def test_discover_local_registry_rejects_missing_tool_descriptor_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    _write_json(
        root / "plugin-a" / "plugin.json",
        {
            "plugin_id": "acme.search",
            "name": "Acme Search",
            "version": "1.0.0",
            "tool_descriptors": ["tools/missing.json"],
        },
    )

    with pytest.raises(FileNotFoundError) as exc:
        discover_local_registry(root)

    assert exc.value.args == (root / "plugin-a" / "tools" / "missing.json",)


def test_discover_local_registry_rejects_tool_descriptor_for_other_plugin(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    _write_json(
        root / "plugin-a" / "plugin.json",
        {
            "plugin_id": "acme.search",
            "name": "Acme Search",
            "version": "1.0.0",
            "tool_descriptors": ["tools/query.json"],
        },
    )
    _write_json(
        root / "plugin-a" / "tools" / "query.json",
        {
            "tool_id": "acme.search.query",
            "plugin_id": "acme.other",
            "name": "Query",
            "description": "Runs a query",
        },
    )

    with pytest.raises(ValueError, match="plugin_id incompatível"):
        discover_local_registry(root)
