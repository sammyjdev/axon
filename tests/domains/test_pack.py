from __future__ import annotations

import json
from pathlib import Path

import pytest

from prometheus.domains import MANIFEST_FILENAME, load_domain_pack


def test_load_domain_pack_accepts_software_fixture() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "software"

    manifest = load_domain_pack(fixture_path)

    assert manifest.schema_version == "1"
    assert manifest.version == "1.0.0"
    assert manifest.domain_id == "software"
    assert manifest.display_name == "Software"
    assert (
        manifest.description == "General software engineering artifacts and workflows."
    )
    assert manifest.default_profiles == ("solo-dev", "team-dev")
    assert manifest.retrieval_defaults == {"top_k": 6, "chunk_size": 1200}
    assert manifest.policy_defaults == {
        "cloud_policy": "avoid",
        "memory_tier": "balanced",
    }
    example = manifest.examples[0]
    assert example.name == "review-change"
    assert (
        example.prompt
        == "Review the following software change for correctness and tests."
    )
    assert example.template == "Change summary:\n{{summary}}\n\nDiff:\n{{diff}}"
    assert manifest.signals.languages == ("python", "typescript")
    assert manifest.signals.artifact_types == ("source_code", "documentation")
    assert manifest.signals.task_types == ("implementation", "review")
    assert manifest.manifest_path == fixture_path / MANIFEST_FILENAME


def test_shipped_domain_packs_support_directory_layout() -> None:
    repo_root = Path(__file__).parents[2]

    for pack_name in ("software", "research", "support", "corporate-use"):
        manifest = load_domain_pack(repo_root / "domain-packs" / pack_name)
        assert manifest.domain_id == pack_name
        assert manifest.manifest_path.name == MANIFEST_FILENAME


def test_load_domain_pack_defaults_legacy_metadata(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / MANIFEST_FILENAME,
        {
            "schema_version": "1",
            "domain_id": "software",
            "display_name": "Software",
            "description": "General software engineering artifacts and workflows.",
            "signals": {"languages": ["python"]},
        },
    )

    loaded = load_domain_pack(manifest)

    assert loaded.version == "1"
    assert loaded.default_profiles == ()
    assert loaded.retrieval_defaults == {}
    assert loaded.policy_defaults == {}
    assert loaded.examples == ()


def test_load_domain_pack_rejects_invalid_domain_id(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / MANIFEST_FILENAME,
        {
            "schema_version": "1",
            "version": "1.0.0",
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
            "version": "1.0.0",
            "domain_id": "software",
            "display_name": "Software",
            "description": "General software engineering artifacts and workflows.",
            "signals": {"languages": ["python", 7]},
        },
    )

    with pytest.raises(ValueError, match="languages entries must be non-empty strings"):
        load_domain_pack(manifest)


def test_load_domain_pack_rejects_invalid_example_entry(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / MANIFEST_FILENAME,
        {
            "schema_version": "1",
            "version": "1.0.0",
            "domain_id": "software",
            "display_name": "Software",
            "description": "General software engineering artifacts and workflows.",
            "examples": [{"name": "review-change"}],
        },
    )

    with pytest.raises(ValueError, match="examples entries must define a prompt or template"):
        load_domain_pack(manifest)


def _write_manifest(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
