"""Tests for axon_record_outcome — the FORGE-Ship → axon write edge.

Closes the dev-OS loop: after a PR ships, the orchestrator records what was
built and how it went. Uses a fake in-memory OutcomeStore so the tool unit test
does not need Postgres (the store's Postgres behavior is covered by
tests/store/test_pg_outcome_store.py).
"""

from __future__ import annotations

import pytest

from axon.mcp import server
from axon.store.outcome_store import OutcomeRecord


class _FakeOutcomeStore:
    def __init__(self) -> None:
        self.saved: list[OutcomeRecord] = []

    async def init(self) -> None:
        return None

    async def save_outcome(self, outcome: OutcomeRecord) -> int:
        self.saved.append(outcome)
        return len(self.saved)


@pytest.fixture
def outcome_store() -> _FakeOutcomeStore:
    return _FakeOutcomeStore()


@pytest.fixture(autouse=True)
def _use_test_outcome_store(
    outcome_store: _FakeOutcomeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_get_outcome_store", lambda: outcome_store)


async def test_record_outcome_persists_record(
    outcome_store: _FakeOutcomeStore,
) -> None:
    out = await server.axon_record_outcome(
        summary="shipped hexagonal ledger use case",
        outcome="PR #42 shipped, gate green, quench 3/3",
        repo="lume",
        context="ship",
        tags=["Rare", "tenant-isolation"],
    )

    assert "recorded outcome" in out and "lume" in out
    assert len(outcome_store.saved) == 1
    rec = outcome_store.saved[0]
    assert rec.project == "lume"
    assert rec.context == "ship"
    assert rec.outcome == "PR #42 shipped, gate green, quench 3/3"
    assert rec.tags == ["Rare", "tenant-isolation"]


async def test_record_outcome_defaults(outcome_store: _FakeOutcomeStore) -> None:
    await server.axon_record_outcome(
        summary="s", outcome="o", repo="axon"
    )
    rec = outcome_store.saved[0]
    assert rec.project == "axon"
    assert rec.context == "ship"
    assert rec.tags == []
