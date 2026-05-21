"""Shared fixtures for the Praxis test suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from praxis.orchestrator import Orchestrator

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def spec_text() -> str:
    """The Markdown spec fixture used to exercise the planner."""
    return (REPO_ROOT / "examples" / "spring-migration.md").read_text(encoding="utf-8")


class PraxisEnv:
    """Owns the MCP server's orchestrator and can simulate a process restart."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._orchestrator: Orchestrator | None = None

    def boot(self) -> Orchestrator:
        """(Re)create the server orchestrator on the same DB — a fresh process."""
        import praxis.server as server

        if self._orchestrator is not None:
            self._orchestrator.close()
        self._orchestrator = Orchestrator(self.db_path)
        server._orchestrator = self._orchestrator
        return self._orchestrator

    def shutdown(self) -> None:
        import praxis.server as server

        if self._orchestrator is not None:
            self._orchestrator.close()
            self._orchestrator = None
        server._orchestrator = None


@pytest.fixture
def praxis_env(tmp_path: Path) -> Iterator[PraxisEnv]:
    env = PraxisEnv(str(tmp_path / "praxis.sqlite"))
    env.boot()
    try:
        yield env
    finally:
        env.shutdown()
