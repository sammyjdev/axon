"""A handoff is a document, and it must survive the session that wrote it.

Seventeen handoffs are in the decision store today and every one of them is a
title with no body: summary and body both average 72 characters, max 80. The
content - state, pending work, next steps - was never saved anywhere. What
remains is a truncated first line, cut mid-sentence:

    "Handoff forge-vs-sdd: SPEC v2 pronto para freeze (falta a palavra 'frozen'), fre"

`axon_handoff` rendered a brief and returned it as text. Whether that text
survived depended on someone pasting it somewhere. The decision store is the
wrong home for it either way: a decision is one line ("chose X because Y"), a
handoff is a document, and squeezing the second into the first keeps only the
title.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axon.mcp import server


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(server, "discover_vault", lambda **_: tmp_path)
    return tmp_path


async def test_the_brief_is_written_to_the_vault(vault: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "recall_context", _fake_recall)

    result = await server.axon_handoff(to_agent="codex", repo="axon")

    written = list((vault / "knowledge" / "handoffs").glob("*.md"))
    assert len(written) == 1, "the brief must outlive the session that produced it"
    assert "recalled context here" in written[0].read_text()
    assert str(written[0]) in result, "the caller needs the path, not just the text"


async def test_the_filename_carries_date_repo_and_target(vault: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "recall_context", _fake_recall)

    await server.axon_handoff(to_agent="codex", repo="axon")

    name = next((vault / "knowledge" / "handoffs").glob("*.md")).name
    assert "axon" in name
    assert "codex" in name
    assert name[:4].isdigit(), "date first, so the directory sorts chronologically"


async def test_caller_notes_are_kept_verbatim(vault: Path, monkeypatch) -> None:
    """What the session actually did is known to the caller, not to axon.

    Recalled context alone reproduces what the store already holds. The part
    worth handing over - what happened in this session, what is half-done - has
    to come from the agent writing the handoff.
    """
    monkeypatch.setattr(server, "recall_context", _fake_recall)

    await server.axon_handoff(
        to_agent="codex",
        repo="axon",
        notes="gate is green; #157 still open; do not rebase the land worktree",
    )

    text = next((vault / "knowledge" / "handoffs").glob("*.md")).read_text()
    assert "do not rebase the land worktree" in text


async def test_no_vault_returns_the_brief_instead_of_failing(monkeypatch) -> None:
    """A missing vault must not cost the caller its handoff."""
    monkeypatch.setattr(server, "discover_vault", lambda **_: None)
    monkeypatch.setattr(server, "recall_context", _fake_recall)

    result = await server.axon_handoff(to_agent="codex", repo="axon")

    assert "recalled context here" in result


async def _fake_recall(repo, store=None, **kwargs):  # noqa: ANN001, ARG001
    return "recalled context here"
