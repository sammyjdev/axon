"""`pb adr sync` writes each project's ADRs into its own ctx folder.

The destination folder IS the ctx: `infer_ctx_from_path` classifies by the first
path component under the vault. Hardcoding "personal" put a work project's ADRs
in vault/personal/adrs/, where indexing treats them as personal and an unscoped
search reaches them - a restricted-context leak, not a cosmetic misfile.
"""
from __future__ import annotations

import json
import types
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from axon.cli import pb
from axon.cli.pb import app

runner = CliRunner()


@dataclass
class _FakeAdr:
    title: str = "t"
    decision: str = "d"
    rationale: str = "r"
    context: str = "c"
    created_at: datetime | None = datetime(2026, 8, 1, tzinfo=UTC)


class _FakeStore:
    """Minimal stand-in: adr_sync only reads projects and their ADRs."""

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def init(self) -> None:
        return None

    async def all_projects(self) -> list[str]:
        return ["afya-poc", "axon", "unlisted-project"]

    async def get_adrs(self, project: str, limit: int = 100) -> list[_FakeAdr]:
        return [_FakeAdr(title=f"adr for {project}")]


def _setup(monkeypatch, tmp_path: Path) -> Path:
    """Point the CLI at a temp vault and a manifest mapping projects to ctx."""
    vault = tmp_path / "vault"
    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    (engine / "config" / "projects.json").write_text(
        json.dumps(
            {
                "projects": [
                    {"name": "afya-poc", "path": "/x", "ctx": "work"},
                    {"name": "axon", "path": "/y", "ctx": "personal"},
                ]
            }
        ),
        encoding="utf-8",
    )
    # _RUNTIME is a frozen dataclass; swap the whole object, as tests/cli/
    # test_index_lock_guard.py does.
    monkeypatch.setattr(
        pb, "_RUNTIME", types.SimpleNamespace(vault_root=vault, engine_root=engine)
    )
    monkeypatch.setattr("axon.cli.pb._get_db_path", lambda: tmp_path / "axon.db")
    monkeypatch.setattr("axon.store.session_store.SessionStore", _FakeStore)
    return vault


def test_work_project_adrs_land_in_the_work_ctx(monkeypatch, tmp_path: Path) -> None:
    vault = _setup(monkeypatch, tmp_path)

    result = runner.invoke(app, ["adr", "sync"])

    assert result.exit_code == 0, result.output
    assert (vault / "work" / "adrs" / "afya-poc.md").exists()
    assert not (vault / "personal" / "adrs" / "afya-poc.md").exists(), (
        "work project's ADRs written under personal/ - unscoped search would reach them"
    )


def test_personal_project_is_unchanged(monkeypatch, tmp_path: Path) -> None:
    vault = _setup(monkeypatch, tmp_path)

    runner.invoke(app, ["adr", "sync"])

    assert (vault / "personal" / "adrs" / "axon.md").exists()


def test_project_missing_from_the_manifest_keeps_the_old_destination(
    monkeypatch, tmp_path: Path
) -> None:
    """Unlisted projects must not break; they keep the previous behaviour."""
    vault = _setup(monkeypatch, tmp_path)

    runner.invoke(app, ["adr", "sync"])

    assert (vault / "personal" / "adrs" / "unlisted-project.md").exists()


def test_unreadable_manifest_does_not_abort_the_sync(monkeypatch, tmp_path: Path) -> None:
    """A corrupt manifest degrades to the old destination instead of failing."""
    vault = _setup(monkeypatch, tmp_path)
    manifest = tmp_path / "engine" / "config" / "projects.json"
    manifest.write_text("{ not json", encoding="utf-8")

    result = runner.invoke(app, ["adr", "sync"])

    assert result.exit_code == 0, result.output
    assert (vault / "personal" / "adrs" / "afya-poc.md").exists()
