from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from axon.context.registry import VALID_CONTEXTS as REGISTERED_CONTEXTS

from .contracts import PluginManifest, ToolDescriptor

VALID_CONTEXTS = set(REGISTERED_CONTEXTS)


@dataclass(frozen=True)
class LocalRegistry:
    plugins: tuple[PluginManifest, ...]
    tools: tuple[ToolDescriptor, ...]

    def list_tools(
        self,
        *,
        ctx: str | None = None,
        pack: str | None = None,
    ) -> tuple[ToolDescriptor, ...]:
        normalized_ctx = _normalize_optional(ctx)
        normalized_pack = _normalize_optional(pack)
        return tuple(
            tool
            for tool in self.tools
            if (normalized_ctx is None or not tool.contexts or normalized_ctx in tool.contexts)
            and (normalized_pack is None or not tool.packs or normalized_pack in tool.packs)
        )


def discover_local_registry(root: Path) -> LocalRegistry:
    if not root.exists():
        return LocalRegistry(plugins=(), tools=())

    plugin_manifests: list[PluginManifest] = []
    tool_descriptors: list[ToolDescriptor] = []
    seen_plugins: set[str] = set()
    seen_tools: set[str] = set()

    for manifest_path in sorted(root.rglob("plugin.json")):
        plugin = load_plugin_manifest(manifest_path)
        if plugin.plugin_id in seen_plugins:
            raise ValueError(f"plugin duplicado no registry local: {plugin.plugin_id}")
        seen_plugins.add(plugin.plugin_id)
        plugin_manifests.append(plugin)

        for descriptor_relpath in plugin.tool_descriptors:
            manifest_dir = manifest_path.parent.resolve()
            descriptor_path = (manifest_dir / descriptor_relpath).resolve()
            if not descriptor_path.is_relative_to(manifest_dir):
                raise ValueError(
                    f"tool_descriptor '{descriptor_relpath}' de '{plugin.plugin_id}' "
                    f"fora do diretório do plugin: {descriptor_path}"
                )
            if not descriptor_path.exists():
                raise FileNotFoundError(descriptor_path)
            tool = load_tool_descriptor(descriptor_path)
            if tool.plugin_id != plugin.plugin_id:
                raise ValueError(
                    f"tool '{tool.tool_id}' usa plugin_id incompatível: {tool.plugin_id}"
                )
            if tool.tool_id in seen_tools:
                raise ValueError(f"tool duplicado no registry local: {tool.tool_id}")
            seen_tools.add(tool.tool_id)
            tool_descriptors.append(tool)

    return LocalRegistry(
        plugins=tuple(sorted(plugin_manifests, key=lambda item: item.plugin_id)),
        tools=tuple(sorted(tool_descriptors, key=lambda item: item.tool_id)),
    )


def load_plugin_manifest(path: Path) -> PluginManifest:
    payload = _load_json(path)
    plugin_id = _require_identifier(
        payload,
        field_name="plugin_id",
        owner=f"manifesto {path}",
    )
    name = _require_text(payload, field_name="name", owner=f"plugin '{plugin_id}'")
    version = _require_text(payload, field_name="version", owner=f"plugin '{plugin_id}'")
    contexts = _read_contexts(payload, owner=f"plugin '{plugin_id}'")
    capability_tags = _read_string_list(
        payload,
        field_name="capability_tags",
        owner=f"plugin '{plugin_id}'",
    )
    raw_tool_descriptors = _read_string_list(
        payload,
        field_name="tool_descriptors",
        owner=f"plugin '{plugin_id}'",
    )
    return PluginManifest(
        plugin_id=plugin_id,
        name=name,
        version=version,
        manifest_path=path.resolve(),
        description=_read_optional_text(payload, field_name="description"),
        enabled=bool(payload.get("enabled", True)),
        contexts=contexts,
        capability_tags=capability_tags,
        tool_descriptors=tuple(Path(item) for item in raw_tool_descriptors),
    )


def load_tool_descriptor(path: Path) -> ToolDescriptor:
    payload = _load_json(path)
    tool_id = _require_identifier(payload, field_name="tool_id", owner=f"tool {path}")
    plugin_id = _require_identifier(
        payload,
        field_name="plugin_id",
        owner=f"tool '{tool_id}'",
    )
    name = _require_text(payload, field_name="name", owner=f"tool '{tool_id}'")
    description = _require_text(
        payload,
        field_name="description",
        owner=f"tool '{tool_id}'",
    )
    contexts = _read_contexts(payload, owner=f"tool '{tool_id}'")
    packs = _read_string_list(payload, field_name="packs", owner=f"tool '{tool_id}'")
    capability_tags = _read_string_list(
        payload,
        field_name="capability_tags",
        owner=f"tool '{tool_id}'",
    )
    return ToolDescriptor(
        tool_id=tool_id,
        plugin_id=plugin_id,
        name=name,
        description=description,
        descriptor_path=path.resolve(),
        contexts=contexts,
        packs=packs,
        capability_tags=capability_tags,
    )


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Arquivo JSON inválido: {path}")
    return payload


def _read_contexts(payload: dict[str, object], *, owner: str) -> tuple[str, ...]:
    contexts = _read_string_list(payload, field_name="contexts", owner=owner)
    invalid = sorted(set(contexts) - VALID_CONTEXTS)
    if invalid:
        raise ValueError(f"{owner} usa ctx inválido: {invalid}")
    return contexts


def _read_string_list(
    payload: dict[str, object],
    *,
    field_name: str,
    owner: str,
) -> tuple[str, ...]:
    raw = payload.get(field_name, [])
    if not isinstance(raw, list):
        raise ValueError(f"{owner} deve declarar '{field_name}' como lista.")
    return tuple(str(item).strip().lower() for item in raw if str(item).strip())


def _require_identifier(
    payload: dict[str, object],
    *,
    field_name: str,
    owner: str,
) -> str:
    value = _require_text(payload, field_name=field_name, owner=owner).lower()
    return value


def _require_text(payload: dict[str, object], *, field_name: str, owner: str) -> str:
    value = str(payload.get(field_name, "")).strip()
    if not value:
        raise ValueError(f"{owner} sem {field_name}.")
    return value


def _read_optional_text(payload: dict[str, object], *, field_name: str) -> str | None:
    value = str(payload.get(field_name, "")).strip()
    return value or None


def _normalize_optional(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None
