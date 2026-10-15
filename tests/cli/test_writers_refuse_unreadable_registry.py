"""A writer must not index when the onboarding registry exists and cannot be read.

Without the registry an already migrated repo resolves to no root, so its files would be
written a second time under their absolute paths, and neither generation's orphan scan can
see the other. Every index entry point is driven here through the real command, with a
truncated registry, and must stop before it opens a store.
"""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from axon.__main__ import app as main_app
from axon.cli import pb
from axon.core.file_identity import load_repo_roots
from axon.expansion.service import ExpansionService

runner = CliRunner()


@pytest.fixture
def truncated_registry_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> types.SimpleNamespace:
    engine = tmp_path / "engine"
    (engine / "data").mkdir(parents=True)
    (engine / "data" / "onboarded_repos.json").write_text('["/repos/alpha"', encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    runtime = types.SimpleNamespace(
        engine_root=engine,
        data_root=engine / "data",
        vault_root=vault,
        pg_url="postgresql://never-opened",
    )

    async def opened() -> tuple[object, object]:
        raise AssertionError("a store was opened before the registry was checked")

    monkeypatch.setattr(pb, "_RUNTIME", runtime)
    monkeypatch.setattr(pb, "_open_file_cache", opened)
    return runtime


def _assert_refused(result: object) -> None:
    exc = result.exception  # type: ignore[attr-defined]
    assert isinstance(exc, ValueError), repr(exc)
    assert "onboarded_repos.json" in str(exc)


@pytest.mark.parametrize("content", ["{corrupt: json", '["/repos/alpha"', '{"a": "/repos/alpha"}'])
def test_strict_load_refuses_a_registry_that_exists_and_cannot_be_read(
    tmp_path: Path, content: str
) -> None:
    (tmp_path / "onboarded_repos.json").write_text(content, encoding="utf-8")
    runtime = types.SimpleNamespace(data_root=tmp_path, vault_root=tmp_path / "vault")

    with pytest.raises(ValueError, match="onboarded_repos.json"):
        load_repo_roots(runtime, strict=True)


def test_strict_load_still_accepts_an_absent_registry(tmp_path: Path) -> None:
    runtime = types.SimpleNamespace(data_root=tmp_path, vault_root=tmp_path / "vault")

    assert load_repo_roots(runtime, strict=True) == {"vault": tmp_path / "vault"}


def test_index_vault_refuses_a_truncated_registry(
    truncated_registry_runtime: types.SimpleNamespace,
) -> None:
    _assert_refused(runner.invoke(main_app, ["index-vault"]))


def test_index_dev_refuses_a_truncated_registry(
    tmp_path: Path, truncated_registry_runtime: types.SimpleNamespace
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    manifest = tmp_path / "projects.json"
    manifest.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "proj",
                        "ctx": "personal",
                        "path": str(project),
                        "languages": ["python"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    _assert_refused(runner.invoke(main_app, ["index-dev", "--manifest", str(manifest)]))


def test_scan_refuses_a_truncated_registry(
    tmp_path: Path, truncated_registry_runtime: types.SimpleNamespace
) -> None:
    scan_root = tmp_path / "dev"
    (scan_root / "proj" / ".git").mkdir(parents=True)

    _assert_refused(runner.invoke(main_app, ["scan", str(scan_root)], input="y\ny\n"))


def test_expansion_reindex_refuses_a_truncated_registry(
    tmp_path: Path, truncated_registry_runtime: types.SimpleNamespace
) -> None:
    service = ExpansionService.__new__(ExpansionService)
    service.runtime = truncated_registry_runtime  # type: ignore[assignment]

    with pytest.raises(ValueError, match="onboarded_repos.json"):
        asyncio.run(service._reindex_publish_path(tmp_path / "vault" / "a.md", "knowledge"))
