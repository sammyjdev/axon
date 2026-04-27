from __future__ import annotations

import json
from pathlib import Path

import pytest

from prometheus.config.projects import load_project_manifest


def _write_manifest(path: Path, projects: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps({"projects": projects}), encoding="utf-8")
    return path


def test_load_project_manifest_accepts_valid_project(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    manifest = _write_manifest(
        tmp_path / "projects.json",
        [
            {
                "name": "Prometheus",
                "path": str(project_root),
                "ctx": "knowledge",
                "enabled": True,
                "languages": ["python", "typescript", "java"],
            }
        ],
    )

    projects = load_project_manifest(manifest)

    assert len(projects) == 1
    assert projects[0].name == "Prometheus"
    assert projects[0].path == project_root
    assert projects[0].ctx == "knowledge"
    assert projects[0].enabled is True
    assert projects[0].languages == ("python", "typescript", "java")


def test_load_project_manifest_rejects_invalid_context(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    manifest = _write_manifest(
        tmp_path / "projects.json",
        [
            {
                "name": "bad-context",
                "path": str(project_root),
                "ctx": "client",
                "languages": ["python"],
            }
        ],
    )

    with pytest.raises(ValueError, match="ctx inválido"):
        load_project_manifest(manifest)


def test_load_project_manifest_rejects_invalid_language(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    manifest = _write_manifest(
        tmp_path / "projects.json",
        [
            {
                "name": "bad-language",
                "path": str(project_root),
                "ctx": "knowledge",
                "languages": ["go"],
            }
        ],
    )

    with pytest.raises(ValueError, match="languages inválidas"):
        load_project_manifest(manifest)


def test_load_project_manifest_rejects_missing_project_path(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing"
    manifest = _write_manifest(
        tmp_path / "projects.json",
        [
            {
                "name": "missing-path",
                "path": str(missing_path),
                "ctx": "knowledge",
                "languages": ["python"],
            }
        ],
    )

    with pytest.raises(FileNotFoundError) as exc:
        load_project_manifest(manifest)

    assert exc.value.args == (missing_path,)
