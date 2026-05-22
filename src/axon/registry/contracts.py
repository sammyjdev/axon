from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator


class PluginManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    plugin_id: str
    name: str
    version: str
    manifest_path: Path
    description: str | None = None
    enabled: bool = True
    contexts: tuple[str, ...] = ()
    capability_tags: tuple[str, ...] = ()
    tool_descriptors: tuple[Path, ...] = ()

    @field_validator("contexts", "capability_tags")
    @classmethod
    def _normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_sorted(value)

    @field_validator("tool_descriptors")
    @classmethod
    def _normalize_descriptors(cls, value: tuple[Path, ...]) -> tuple[Path, ...]:
        return _unique_paths(value)


class ToolDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_id: str
    plugin_id: str
    name: str
    description: str
    descriptor_path: Path
    contexts: tuple[str, ...] = ()
    packs: tuple[str, ...] = ()
    capability_tags: tuple[str, ...] = ()

    @field_validator("contexts", "packs", "capability_tags")
    @classmethod
    def _normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_sorted(value)


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
