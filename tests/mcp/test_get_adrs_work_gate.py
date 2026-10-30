"""get_adrs keeps work projects behind ctx='work', named only by the local manifest."""

from __future__ import annotations

import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

from axon.mcp import server
from axon.store.session_store import ADR


class _FakeStore:
    async def init(self) -> None:
        return None

    async def get_adrs(self, project: str) -> list[ADR]:
        return [
            ADR(
                project=project,
                title="Private ADR",
                context="ctx",
                decision="dec",
                rationale="rat",
                provenance="human",
                created_at=datetime.now(UTC),
            )
        ]


def _engine_with_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.json").write_text(text, encoding="utf-8")
    monkeypatch.setattr(server, "_RUNTIME", types.SimpleNamespace(engine_root=tmp_path))
    monkeypatch.setattr(server, "_get_session_store", lambda: _FakeStore())


async def test_a_work_project_is_refused_without_the_work_ctx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _engine_with_manifest(
        monkeypatch, tmp_path, '{"projects": [{"name": "Client-A", "ctx": "work"}]}'
    )

    result = await server.get_adrs(project="client-a")

    assert result == "Contexto de trabalho requer ctx='work' explícito."


async def test_a_work_project_is_readable_with_the_work_ctx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _engine_with_manifest(
        monkeypatch, tmp_path, '{"projects": [{"name": "Client-A", "ctx": "work"}]}'
    )

    result = await server.get_adrs(project="client-a", ctx="work")

    assert "### Private ADR" in result


async def test_a_personal_project_needs_no_ctx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _engine_with_manifest(
        monkeypatch, tmp_path, '{"projects": [{"name": "axon", "ctx": "personal"}]}'
    )

    result = await server.get_adrs(project="axon")

    assert "### Private ADR" in result


async def test_an_unreadable_manifest_does_not_break_the_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _engine_with_manifest(monkeypatch, tmp_path, "{ not json")

    result = await server.get_adrs(project="axon")

    assert "### Private ADR" in result
