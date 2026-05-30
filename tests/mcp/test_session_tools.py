"""Tests for the AXON MCP session-lifecycle tools (T3.3)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from axon.mcp import server
from axon.store.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture(autouse=True)
def _use_test_store(store: SessionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_get_session_store", lambda: store)


async def test_session_start_returns_id_and_recalled_context() -> None:
    out = await server.axon_session_start(agent="claude-code", repo="axon")
    assert "session:" in out
    assert "repo: axon" in out
    assert "AXON recall" in out  # recall_context header


async def test_session_start_then_end_saves_summary_note(
    store: SessionStore,
) -> None:
    start = await server.axon_session_start(agent="claude-code", repo="axon")
    session_id = start.split("session:")[1].split()[0]

    out = await server.axon_session_end(session_id, summary="finished the migration")
    assert "ended" in out and "axon" in out

    notes = await store.get_notes("axon")
    assert any("finished the migration" in note.body for note in notes)


async def test_session_end_unknown_id() -> None:
    out = await server.axon_session_end("deadbeef0000")
    assert "not found" in out


async def test_get_session_memory_surfaces_captured_decisions(
    store: SessionStore,
) -> None:
    # A decision captured via axon_capture (status="draft") must show up in
    # get_session_memory even when no compressed session summary or note exists
    # yet — otherwise captured work is invisible until the PostStop hook runs.
    from datetime import UTC, datetime

    from axon.core.decision import Decision

    decision = Decision(
        id=await store.next_decision_id(),
        timestamp=datetime.now(UTC),
        agent="manual",
        repo="axon",
        summary="captured: hardened the indexer against venv pollution",
        status="draft",
    )
    await store.save_decision(decision)

    out = await server.get_session_memory(project="axon")

    assert "Nenhuma memória" not in out
    assert "hardened the indexer against venv pollution" in out


async def test_capture_event_persists_note(store: SessionStore) -> None:
    out = await server.axon_capture_event(
        "test_pass", {"repo": "axon", "passed": 469}
    )
    assert "captured test_pass for axon" in out

    notes = await store.get_notes("axon")
    assert any("test_pass" in note.body and "469" in note.body for note in notes)
