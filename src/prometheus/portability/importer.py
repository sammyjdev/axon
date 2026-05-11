from __future__ import annotations

import hashlib
import json
from pathlib import Path

from prometheus.portability.exporter import EXPORT_MANIFEST_VERSION, ExportArtifact, ExportManifest

_MANIFEST_FILENAME = "manifest.json"


def import_portability_bundle(bundle_root: str | Path, engine_root: str | Path) -> ExportManifest:
    source_root = Path(bundle_root)
    destination_root = Path(engine_root)
    manifest = _load_manifest(source_root)

    if manifest.manifest_version != EXPORT_MANIFEST_VERSION:
        raise ValueError(
            f"Unsupported portability manifest version: {manifest.manifest_version}"
        )

    destination_root.mkdir(parents=True, exist_ok=True)
    for artifact in manifest.artifacts:
        source_path = source_root / artifact.path
        if not source_path.exists():
            raise FileNotFoundError(source_path)
        payload = source_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != artifact.sha256:
            raise ValueError(f"Checksum mismatch for portability artifact: {artifact.path}")
        destination_path = _destination_path(destination_root, artifact.path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.write_bytes(payload)

    return manifest


def _load_manifest(bundle_root: Path) -> ExportManifest:
    manifest_path = bundle_root / _MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = tuple(ExportArtifact(**artifact) for artifact in payload.get("artifacts", []))
    return ExportManifest(
        manifest_version=str(payload.get("manifest_version", "")),
        artifacts=artifacts,
    )


def _destination_path(engine_root: Path, bundle_path: str) -> Path:
    relative_path = Path(bundle_path)
    if relative_path.parts[:1] == ("stores",):
        if len(relative_path.parts) == 1:
            raise ValueError("Invalid portability artifact path: stores")
        return engine_root / "data" / Path(*relative_path.parts[1:])
    if relative_path.parts[:1] in {("config",), ("metadata",)}:
        return engine_root / relative_path
    raise ValueError(f"Unsupported portability artifact path: {bundle_path}")
