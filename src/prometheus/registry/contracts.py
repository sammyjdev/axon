from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    manifest_path: Path
    description: str | None = None
    enabled: bool = True
    contexts: tuple[str, ...] = ()
    capability_tags: tuple[str, ...] = ()
    tool_descriptors: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "contexts", _unique_sorted(self.contexts))
        object.__setattr__(self, "capability_tags", _unique_sorted(self.capability_tags))
        object.__setattr__(self, "tool_descriptors", _unique_paths(self.tool_descriptors))


@dataclass(frozen=True)
class ToolDescriptor:
    tool_id: str
    plugin_id: str
    name: str
    description: str
    descriptor_path: Path
    contexts: tuple[str, ...] = ()
    packs: tuple[str, ...] = ()
    capability_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "contexts", _unique_sorted(self.contexts))
        object.__setattr__(self, "packs", _unique_sorted(self.packs))
        object.__setattr__(self, "capability_tags", _unique_sorted(self.capability_tags))


def _unique_sorted(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    normalized = {str(value).strip().lower() for value in values if str(value).strip()}
    return tuple(sorted(normalized))


def _unique_paths(values: tuple[Path, ...] | list[Path]) -> tuple[Path, ...]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        path = Path(value)
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return tuple(unique)
