from __future__ import annotations

import json
from pathlib import Path

import pytest

from prometheus.domains import MANIFEST_FILENAME, load_domain_pack


def test_load_domain_pack_accepts_software_fixture() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "software"

    manifest = load_domain_pack(fixture_path)

    assert manifest.schema_version == "1"
    assert manifest.domain_id == "software"
    assert manifest.display_name == "Software"
    assert manifest.description == "General software engineering artifacts and workflows."
    assert manifest.signals.languages == ("python", "typescript")
    assert manifest.signals.artifact_types == ("source_code", "documentation")
    assert manifest.signals.task_types == ("implementation", "review")
    assert manifest.manifest_path == fixture_path / MANIFEST_FILENAME


def test_load_domain_pack_rejects_invalid_domain_id(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / MANIFEST_FILENAME,
        {
            "schema_version": "1",
            "domain_id": "Software Domain",
            "display_name": "Software",
            "description": "General software engineering artifacts and workflows.",
        },
    )

    with pytest.raises(ValueError, match="Invalid domain_id"):
        load_domain_pack(manifest)


def test_load_domain_pack_rejects_non_string_signal_entries(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / MANIFEST_FILENAME,
        {
            "schema_version": "1",
            "domain_id": "software",
            "display_name": "Software",
            "description": "General software engineering artifacts and workflows.",
            "signals": {"languages": ["python", 7]},
        },
    )

    with pytest.raises(ValueError, match="languages entries must be non-empty strings"):
        load_domain_pack(manifest)


def _write_manifest(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
