from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

MANIFEST_FILENAME = "domain-pack.json"
SCHEMA_VERSION = "1"
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True)
class DomainSignals:
    languages: tuple[str, ...] = ()
    artifact_types: tuple[str, ...] = ()
    task_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainPackManifest:
    schema_version: str
    domain_id: str
    display_name: str
    description: str
    signals: DomainSignals
    manifest_path: Path


def load_domain_pack(path: Path) -> DomainPackManifest:
    manifest_path = _resolve_manifest_path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Domain pack manifest must be a JSON object.")

    schema_version = _require_string(payload, "schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"Unsupported domain pack schema_version: {schema_version}")

    domain_id = _require_string(payload, "domain_id")
    if not _IDENTIFIER_PATTERN.fullmatch(domain_id):
        raise ValueError(f"Invalid domain_id: {domain_id}")

    signals_payload = payload.get("signals", {})
    if not isinstance(signals_payload, dict):
        raise ValueError("signals must be an object.")

    return DomainPackManifest(
        schema_version=schema_version,
        domain_id=domain_id,
        display_name=_require_string(payload, "display_name"),
        description=_require_string(payload, "description"),
        signals=DomainSignals(
            languages=_string_list(signals_payload, "languages"),
            artifact_types=_string_list(signals_payload, "artifact_types"),
            task_types=_string_list(signals_payload, "task_types"),
        ),
        manifest_path=manifest_path,
    )


def _resolve_manifest_path(path: Path) -> Path:
    manifest_path = path / MANIFEST_FILENAME if path.is_dir() else path
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    return manifest_path


def _require_string(payload: dict[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _string_list(payload: dict[str, object], field_name: str) -> tuple[str, ...]:
    value = payload.get(field_name, [])
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list.")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} entries must be non-empty strings.")
        normalized_item = item.strip()
        if normalized_item in seen:
            raise ValueError(f"{field_name} entries must be unique.")
        seen.add(normalized_item)
        normalized.append(normalized_item)

    return tuple(normalized)
