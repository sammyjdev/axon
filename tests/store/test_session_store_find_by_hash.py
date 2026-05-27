from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from axon.core.decision import Decision
from axon.store.session_store import SessionStore


def _decision(*, id: str, git_hash: str, repo: str = "axon") -> Decision:
    return Decision(
        id=id,
        timestamp=datetime.now(UTC),
        agent="claude-code",
        repo=repo,
        summary=f"summary for {id}",
        git_hash=git_hash,
        status="draft",
    )


@pytest.mark.asyncio
async def test_find_decision_by_git_hash_returns_existing(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    try:
        await store.save_decision(_decision(id="dec-001", git_hash="abc123"))
        await store.save_decision(_decision(id="dec-002", git_hash="def456"))

        found = await store.find_decision_by_git_hash("abc123")

        assert found is not None
        assert found.id == "dec-001"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_find_decision_by_git_hash_returns_none_when_absent(
    tmp_path: Path,
) -> None:
    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    try:
        await store.save_decision(_decision(id="dec-001", git_hash="abc123"))

        assert await store.find_decision_by_git_hash("nope") is None
    finally:
        await store.close()
