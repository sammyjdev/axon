"""get_adrs keeps work projects behind ctx='work', named only by the environment."""

from __future__ import annotations

from datetime import UTC, datetime

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


@pytest.fixture(autouse=True)
def _fake_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_get_session_store", lambda: _FakeStore())


async def test_a_work_project_is_refused_without_the_work_ctx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AXON_WORK_PROJECTS", "other, Client-A")

    result = await server.get_adrs(project="client-a")

    assert result == "Contexto de trabalho requer ctx='work' explícito."


async def test_a_work_project_is_readable_with_the_work_ctx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AXON_WORK_PROJECTS", "client-a")

    result = await server.get_adrs(project="client-a", ctx="work")

    assert "### Private ADR" in result


async def test_a_project_not_listed_needs_no_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AXON_WORK_PROJECTS", "client-a")

    result = await server.get_adrs(project="axon")

    assert "### Private ADR" in result


async def test_without_the_variable_no_project_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AXON_WORK_PROJECTS", raising=False)

    result = await server.get_adrs(project="axon")

    assert "### Private ADR" in result
