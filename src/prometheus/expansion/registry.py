from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from prometheus.config.runtime import RuntimeConfig, load_runtime_config
from prometheus.expansion.models import (
    ExtractionMode,
    JsonFieldMap,
    SourceDefinition,
    SourceFormat,
)


class UnknownSourceError(ValueError):
    pass


class DuplicateSourceError(ValueError):
    pass


@dataclass(frozen=True)
class SourceRegistry:
    _sources: dict[str, SourceDefinition]

    def __init__(self, sources: list[SourceDefinition] | tuple[SourceDefinition, ...]) -> None:
        registry: dict[str, SourceDefinition] = {}
        for source in sources:
            if source.source_id in registry:
                raise DuplicateSourceError(f"duplicate source id: {source.source_id}")
            registry[source.source_id] = source
        object.__setattr__(self, "_sources", registry)

    def get(self, source_id: str) -> SourceDefinition:
        try:
            return self._sources[source_id]
        except KeyError as exc:
            raise UnknownSourceError(f"source not registered: {source_id}") from exc

    def list_for_context(self, ctx: str) -> list[SourceDefinition]:
        return sorted(
            (source for source in self._sources.values() if ctx in source.allowed_contexts),
            key=lambda source: source.source_id,
        )

    def all(self) -> list[SourceDefinition]:
        return sorted(self._sources.values(), key=lambda source: source.source_id)


def default_source_registry(runtime: RuntimeConfig | None = None) -> SourceRegistry:
    return load_source_registry(runtime=runtime)


def load_source_registry(
    catalog_path: Path | None = None,
    runtime: RuntimeConfig | None = None,
) -> SourceRegistry:
    active_runtime = runtime or load_runtime_config()
    path = catalog_path or active_runtime.expansion.source_catalog_path
    if not path.exists():
        return SourceRegistry(())

    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("sources", [])
    if not isinstance(rows, list):
        raise ValueError("source catalog deve conter uma lista em 'sources'")
    return SourceRegistry(tuple(_parse_source_definition(row) for row in rows))


def _parse_source_definition(payload: dict[str, object]) -> SourceDefinition:
    if not isinstance(payload, dict):
        raise ValueError("source definition invalida")

    json_fields_payload = payload.get("json_fields")
    json_fields = None
    if isinstance(json_fields_payload, dict):
        json_fields = JsonFieldMap(
            title=tuple(json_fields_payload.get("title", ())),
            url=tuple(json_fields_payload.get("url", ())),
            published_at=tuple(json_fields_payload.get("published_at", ())),
            summary=tuple(json_fields_payload.get("summary", ())),
            content=tuple(json_fields_payload.get("content", ())),
        )

    headers_payload = payload.get("headers", [])
    headers: tuple[tuple[str, str], ...] = ()
    if isinstance(headers_payload, list):
        headers = tuple(
            (str(item[0]), str(item[1]))
            for item in headers_payload
            if isinstance(item, (list, tuple)) and len(item) == 2
        )

    return SourceDefinition(
        source_id=str(payload["source_id"]),
        name=str(payload["name"]),
        endpoint=str(payload["endpoint"]),
        format=SourceFormat(str(payload["format"])),
        allowed_contexts=tuple(str(item) for item in payload.get("allowed_contexts", ())),
        extraction_mode=ExtractionMode(str(payload.get("extraction_mode", "feed"))),
        follow_links=bool(payload.get("follow_links", False)),
        json_items_path=tuple(payload.get("json_items_path", ())),
        json_fields=json_fields,
        headers=headers,
    )
