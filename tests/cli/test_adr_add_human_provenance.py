"""`axon adr add` is the only path allowed to claim human authorship.

The provenance field from #92 is fail-closed: everything defaults to
`llm-inferred` and one path opts out. Which makes that opt-out the property
worth pinning — and it had no test at all. Deleting the `provenance="human"`
line from `cli/pb.py` left the whole of `tests/cli/` green (141 passed), while
`save_adr` shipped the mirror-image defect: an MCP tool claiming human
authorship for an agent's writes.

What makes this path legitimate is not the code, it is the `typer.prompt()` in
front of it: a person at a terminal typed the content. That is why the test
drives the command the way a person would, through the entry-point app.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from typer.testing import CliRunner

from axon.__main__ import app
from axon.store.session_store import SessionStore

runner = CliRunner()


def test_adr_add_records_human_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "typed by a person")
    # Unique per run: the store is not truncated between runs, and reading
    # adrs[0] of a shared project name picked up the PREVIOUS run's row - which
    # made this test pass against a mutant that deleted the line under test.
    project = f"test-adr-add-human-{uuid.uuid4().hex[:8]}"

    # Sync on purpose: the command calls asyncio.run() internally, which raises
    # if the test itself is already running an event loop.
    result = runner.invoke(
        app,
        ["adr", "add", "--project", project, "--title", "Human ADR"],
    )
    assert result.exit_code == 0, result.exception or result.output

    async def _read() -> list:
        store = SessionStore()
        await store.init()
        return await store.get_adrs(project)

    adrs = asyncio.run(_read())
    assert len(adrs) == 1
    assert adrs[0].provenance == "human", (
        "adr add is the one path that may claim human authorship, and nothing "
        "else pins that - deleting the line left tests/cli/ fully green"
    )
