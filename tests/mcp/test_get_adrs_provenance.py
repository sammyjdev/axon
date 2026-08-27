"""Tests for ADR provenance handling in MCP tools (get_adrs, save_adr)."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest

from axon.mcp import server
from axon.store.session_store import ADR, ADR_INFERRED_NOTICE, SessionStore


@pytest.fixture
async def store() -> AsyncGenerator[SessionStore, None]:
    s = SessionStore()
    await s.init()
    yield s
    await s.close()


@pytest.fixture(autouse=True)
def _use_test_store(store: SessionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_get_session_store", lambda: store)


async def test_get_adrs_tool_surfaces_the_machine_inferred_label(
    store: SessionStore,
) -> None:
    proj = "test-get-adrs"

    adr_inferred = ADR(
        project=proj,
        title="Inferred ADR Title",
        context="ctx",
        decision="dec",
        rationale="rat",
        provenance="llm-inferred",
        created_at=datetime.now(UTC),
    )
    adr_human = ADR(
        project=proj,
        title="Human ADR Title",
        context="ctx",
        decision="dec",
        rationale="rat",
        provenance="human",
        created_at=datetime.now(UTC),
    )

    await store.save_adr(adr_inferred)
    await store.save_adr(adr_human)

    result = await server.get_adrs(project=proj)

    inferred_pos = result.find("### Inferred ADR Title")
    human_pos = result.find("### Human ADR Title")
    assert inferred_pos != -1
    assert human_pos != -1

    if inferred_pos < human_pos:
        inferred_block = result[inferred_pos:human_pos]
        human_block = result[human_pos:]
    else:
        human_block = result[human_pos:inferred_pos]
        inferred_block = result[inferred_pos:]

    assert ADR_INFERRED_NOTICE in inferred_block
    assert ADR_INFERRED_NOTICE not in human_block


async def test_save_adr_tool_cannot_claim_human_authorship(
    store: SessionStore,
) -> None:
    """`save_adr` is an MCP tool: the caller is an agent, not a person.

    Its own docstring tells the calling model to use it "quando tomar uma
    decisao de design relevante", so the writer is an LLM by construction.
    Hardcoding provenance="human" here let any connected agent insert an ADR
    labelled as human-authored with no human involved, which is exactly what
    the provenance field exists to prevent. The only path that may claim
    human authorship is `pb adr add`, which is gated behind a typer.prompt().
    """
    proj = "test-save-adr"
    title = "Agent Saved ADR"

    msg = await server.save_adr(
        project=proj,
        title=title,
        context="ctx test",
        decision="dec test",
        rationale="rat test",
    )
    assert f"ADR salvo: '{title}'" in msg

    adrs = await store.get_adrs(proj)
    assert len(adrs) == 1
    assert adrs[0].title == title
    assert adrs[0].provenance == "llm-inferred"
