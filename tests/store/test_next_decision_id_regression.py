"""Regression tests for next_decision_id using max(id)+1 instead of count+1.

With a non-contiguous id sequence (dec-001, dec-003 — no dec-002), count+1
returns 3, yielding dec-003 which already exists. max+1 must return dec-004.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from axon.core.decision import Decision
from axon.store.session_store import SessionStore


def _decision(did: str) -> Decision:
    return Decision(
        id=did,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        agent="manual",
        repo="axon",
        summary="regression test decision",
    )


@pytest.mark.asyncio
async def test_sqlite_next_decision_id_non_contiguous_gap(tmp_path: Path) -> None:
    """Regression: SQLite next_decision_id returns max+1, not count+1.

    Insert dec-001 and dec-003 (gap at dec-002, count=2). count+1 => dec-003
    (existing id, collision). max+1 must yield dec-004.
    """
    # Force SQLite backend so this test does not depend on env config.
    os.environ["AXON_DECISIONS_BACKEND"] = "sqlite"
    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    try:
        await store.save_decision(_decision("dec-001"))
        await store.save_decision(_decision("dec-003"))
        result = await store.next_decision_id()
        assert result == "dec-004", (
            f"expected dec-004 (max=3, +1), got {result!r}; "
            "count+1 would return dec-003, colliding with existing id"
        )
    finally:
        await store.close()
        os.environ.pop("AXON_DECISIONS_BACKEND", None)
