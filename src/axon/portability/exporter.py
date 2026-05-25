from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from axon.config.runtime import get_axon_config_path, load_runtime_config
from axon.context.registry import VALID_CONTEXTS

EXPORT_MANIFEST_VERSION = "1"

_ENV_EXPORT_ALLOWLIST: tuple[str, ...] = (
    "AXON_CONFIG",
    "AXON_ENGINE",
    "AXON_VAULT",
    "AXON_RUNTIME_MODE",
    "AXON_OLLAMA_LOCAL_HOST",
)


class RuntimeLike(Protocol):
    engine_root: Path

    @property
    def data_root(self) -> Path: ...


class ExportArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    path: str
    sha256: str
    size_bytes: int


class ExportManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest_version: str
    artifacts: tuple[ExportArtifact, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "artifacts": [artifact.model_dump() for artifact in self.artifacts],
            "manifest_version": self.manifest_version,
        }


def export_portability_bundle(
    destination: str | Path,
    *,
    runtime: RuntimeLike | None = None,
) -> ExportManifest:
    resolved_runtime = runtime or load_runtime_config()
    export_root = Path(destination)
    export_root.mkdir(parents=True, exist_ok=True)

    artifacts: list[ExportArtifact] = []
    config_path = get_axon_config_path()
    if config_path.exists():
        artifacts.append(
            _write_copied_artifact(
                source_path=config_path,
                export_root=export_root,
                relative_path=Path("config") / "axon.toml",
                kind="config/axon_toml",
            )
        )

    env_payload = {"entries": _build_env_metadata_entries()}
    artifacts.append(
        _write_json_artifact(
            payload=env_payload,
            export_root=export_root,
            relative_path=Path("metadata") / "env.json",
            kind="metadata/env",
        )
    )
    indexed_contexts_payload = {
        "contexts": list(VALID_CONTEXTS),
        "manifest_version": EXPORT_MANIFEST_VERSION,
    }
    artifacts.append(
        _write_json_artifact(
            payload=indexed_contexts_payload,
            export_root=export_root,
            relative_path=Path("metadata") / "indexed-contexts.json",
            kind="metadata/indexed_contexts",
        )
    )

    store_artifacts = (
        (
            "store/trace",
            resolved_runtime.data_root / "trace" / "records.jsonl",
            Path("stores/trace/records.jsonl"),
        ),
        ("store/failure", resolved_runtime.data_root / "failures.db", Path("stores/failures.db")),
        ("store/outcome", resolved_runtime.data_root / "outcomes.db", Path("stores/outcomes.db")),
    )
    for kind, source_path, relative_path in store_artifacts:
        if source_path.exists():
            artifacts.append(
                _write_copied_artifact(
                    source_path=source_path,
                    export_root=export_root,
                    relative_path=relative_path,
                    kind=kind,
                )
            )

    manifest = ExportManifest(
        manifest_version=EXPORT_MANIFEST_VERSION,
        artifacts=tuple(sorted(artifacts, key=lambda artifact: (artifact.kind, artifact.path))),
    )
    manifest_path = export_root / "manifest.json"
    manifest_path.write_text(_render_json(manifest.to_payload()), encoding="utf-8")
    return manifest


def _build_env_metadata_entries() -> list[dict[str, object]]:
    entries = [
        {"name": name, "present": True, "source": "env"}
        for name in sorted(_ENV_EXPORT_ALLOWLIST)
        if name in os.environ
    ]
    return entries


def _write_copied_artifact(
    *,
    source_path: Path,
    export_root: Path,
    relative_path: Path,
    kind: str,
) -> ExportArtifact:
    payload = source_path.read_bytes()
    destination = export_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return _artifact_from_bytes(kind=kind, relative_path=relative_path, payload=payload)


def _write_json_artifact(
    *,
    payload: dict[str, object],
    export_root: Path,
    relative_path: Path,
    kind: str,
) -> ExportArtifact:
    rendered = _render_json(payload).encode("utf-8")
    destination = export_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(rendered)
    return _artifact_from_bytes(kind=kind, relative_path=relative_path, payload=rendered)


def _artifact_from_bytes(
    *,
    kind: str,
    relative_path: Path,
    payload: bytes,
) -> ExportArtifact:
    return ExportArtifact(
        kind=kind,
        path=relative_path.as_posix(),
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def _render_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
