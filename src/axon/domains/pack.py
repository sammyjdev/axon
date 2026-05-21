from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
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
class DomainPackExample:
    name: str
    prompt: str | None = None
    template: str | None = None


@dataclass(frozen=True)
class DomainPackManifest:
    schema_version: str
    version: str
    domain_id: str
    display_name: str
    description: str
    signals: DomainSignals
    manifest_path: Path
    default_profiles: tuple[str, ...] = ()
    retrieval_defaults: dict[str, object] = field(default_factory=dict)
    policy_defaults: dict[str, object] = field(default_factory=dict)
    examples: tuple[DomainPackExample, ...] = ()


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
        version=_optional_string(payload, "version") or schema_version,
        domain_id=domain_id,
        display_name=_require_string(payload, "display_name"),
        description=_require_string(payload, "description"),
        default_profiles=_string_list(payload, "default_profiles"),
        retrieval_defaults=_dict_payload(payload, "retrieval_defaults"),
        policy_defaults=_dict_payload(payload, "policy_defaults"),
        examples=_example_list(payload),
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


def _optional_string(payload: dict[str, object], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
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


def _dict_payload(payload: dict[str, object], field_name: str) -> dict[str, object]:
    value = payload.get(field_name, {})
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object.")
    return dict(value)


def _example_list(payload: dict[str, object]) -> tuple[DomainPackExample, ...]:
    value = payload.get("examples", [])
    if not isinstance(value, list):
        raise ValueError("examples must be a list.")

    examples: list[DomainPackExample] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("examples entries must be objects.")

        name = _require_string(item, "name")
        if name in seen:
            raise ValueError("examples entries must have unique names.")
        seen.add(name)

        prompt = _optional_string(item, "prompt")
        template = _optional_string(item, "template")
        if prompt is None and template is None:
            raise ValueError("examples entries must define a prompt or template.")

        examples.append(DomainPackExample(name=name, prompt=prompt, template=template))

    return tuple(examples)
