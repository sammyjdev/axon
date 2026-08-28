"""The familiar timeline must not render an inferred ADR as an authored one.

`get_adrs` and `axon adr list` both carry ADR_INFERRED_NOTICE. This renderer
reads the adr table directly and did not, so a poisoned title appeared in the
timeline looking exactly like a decision a person wrote. The cross-review
request for PR #160 asked whether any other retrieval path reads the table
directly; this was the one, and nobody answered it at the time.
"""

from __future__ import annotations

import asyncio
import datetime

from axon.pet import familiar
from axon.store.session_store import ADR


def _adr(title: str, provenance: str) -> ADR:
    return ADR(
        project="p",
        title=title,
        context="c",
        decision="d",
        rationale="r",
        provenance=provenance,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _render(adr: ADR, monkeypatch) -> str:
    class _Store:
        def __init__(self, *a, **k) -> None: ...
        async def init(self) -> None: ...
        async def close(self) -> None: ...
        async def all_projects(self) -> list[str]:
            return ["p"]

        async def get_adrs(self, project: str, limit: int = 0) -> list[ADR]:
            return [adr]

    # familiar imports SessionStore inside the function, so the patch has to
    # land on the source module, not on `familiar`.
    monkeypatch.setattr("axon.store.session_store.SessionStore", _Store)
    _count, moments = asyncio.run(familiar.fetch_adr_data())
    return moments[0].text


def test_an_inferred_adr_is_marked(monkeypatch) -> None:
    assert _render(_adr("Short title", "llm-inferred"), monkeypatch).startswith("~")


def test_a_human_adr_is_not_marked(monkeypatch) -> None:
    assert _render(_adr("Short title", "human"), monkeypatch) == "Short title"


def test_the_marker_survives_truncation(monkeypatch) -> None:
    """A long title must not push the marker out of the 26-column budget."""
    text = _render(_adr("x" * 80, "llm-inferred"), monkeypatch)

    assert text.startswith("~")
    assert len(text) <= 26
