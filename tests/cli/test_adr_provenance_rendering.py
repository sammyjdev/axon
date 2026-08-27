"""Tests for rendering ADR provenance notice in CLI outputs.

Vault export matters because it is the only path by which ask sees an ADR
(ask reads vault markdown files and does not call get_adrs).
"""
from __future__ import annotations

import json
import types
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from axon.__main__ import app
from axon.cli import pb
from axon.store.session_store import ADR_INFERRED_NOTICE

runner = CliRunner()


@dataclass
class _FakeAdr:
    title: str = "t"
    decision: str = "d"
    rationale: str = "r"
    context: str = "c"
    provenance: str = "llm-inferred"
    created_at: datetime | None = datetime(2026, 8, 1, tzinfo=UTC)


class _FakeStore:
    """Minimal stand-in for session store in CLI tests."""

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def init(self) -> None:
        return None

    async def all_projects(self) -> list[str]:
        return ["axon"]

    async def get_adrs(self, project: str, limit: int = 100) -> list[_FakeAdr]:
        return [
            _FakeAdr(title="Inferred ADR", provenance="llm-inferred"),
            _FakeAdr(title="Human ADR", provenance="human"),
        ]


def _setup(monkeypatch, tmp_path: Path) -> Path:
    """Point the CLI at a temp vault and a manifest mapping projects to ctx."""
    vault = tmp_path / "vault"
    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    (engine / "config" / "projects.json").write_text(
        json.dumps(
            {
                "projects": [
                    {"name": "axon", "path": "/y", "ctx": "personal"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        pb, "_RUNTIME", types.SimpleNamespace(vault_root=vault, engine_root=engine)
    )
    monkeypatch.setattr("axon.cli.pb._get_db_path", lambda: tmp_path / "axon.db")
    monkeypatch.setattr("axon.store.session_store.SessionStore", _FakeStore)
    return vault


def test_vault_export_labels_inferred_adrs(monkeypatch, tmp_path: Path) -> None:
    """adr sync writes ADRs into vault markdown.

    This file matters because it is the only path by which ask sees an ADR
    (ask reads vault markdown and does not call get_adrs).
    """
    vault = _setup(monkeypatch, tmp_path)

    result = runner.invoke(app, ["adr", "sync"])

    assert result.exit_code == 0, result.output
    exported_file = vault / "personal" / "adrs" / "axon.md"
    assert exported_file.exists()
    content = exported_file.read_text(encoding="utf-8")

    inferred_pos = content.find("## Inferred ADR")
    human_pos = content.find("## Human ADR")
    assert inferred_pos != -1
    assert human_pos != -1

    if inferred_pos < human_pos:
        inferred_section = content[inferred_pos:human_pos]
        human_section = content[human_pos:]
    else:
        human_section = content[human_pos:inferred_pos]
        inferred_section = content[inferred_pos:]

    assert ADR_INFERRED_NOTICE in inferred_section
    assert ADR_INFERRED_NOTICE not in human_section


def test_adr_list_labels_inferred_adrs(monkeypatch, tmp_path: Path) -> None:
    """adr list shows the notice only for non-human ADRs."""
    _setup(monkeypatch, tmp_path)

    result = runner.invoke(app, ["adr", "list", "-p", "axon"])

    assert result.exit_code == 0, result.output
    output = result.output

    inferred_pos = output.find("# Inferred ADR")
    human_pos = output.find("# Human ADR")
    assert inferred_pos != -1
    assert human_pos != -1

    if inferred_pos < human_pos:
        inferred_block = output[inferred_pos:human_pos]
        human_block = output[human_pos:]
    else:
        human_block = output[human_pos:inferred_pos]
        inferred_block = output[inferred_pos:]

    assert ADR_INFERRED_NOTICE in inferred_block
    assert ADR_INFERRED_NOTICE not in human_block
