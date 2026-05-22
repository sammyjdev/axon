"""Tests for the unified recall strategy (T2.5)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from axon.core.decision import Decision
from axon.recall.strategy import recall_context
from axon.store.session_store import SessionStore

_NOW = datetime.now(UTC)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


def _decision(**overrides: Any) -> Decision:
    base: dict[str, Any] = dict(
        id="dec-001",
        timestamp=_NOW,
        agent="claude-code",
        repo="axon",
        summary="a decision",
    )
    base.update(overrides)
    return Decision(**base)


async def test_recall_empty_repo(store: SessionStore) -> None:
    out = await recall_context("axon", store=store)
    assert "no decisions recalled" in out


async def test_recall_lists_repo_decisions(store: SessionStore) -> None:
    await store.save_decision(_decision(id="dec-001", summary="alpha decision"))
    out = await recall_context("axon", store=store)
    assert "dec-001" in out and "alpha decision" in out


async def test_symbol_match_outranks_plain_repo_decision(store: SessionStore) -> None:
    await store.save_decision(
        _decision(
            id="dec-100",
            summary="touches the symbol",
            timestamp=_NOW - timedelta(days=30),
            symbols=["pkg.module.Sym"],
        )
    )
    await store.save_decision(
        _decision(id="dec-200", summary="recent but unrelated", timestamp=_NOW)
    )
    out = await recall_context("axon", symbols=["pkg.module.Sym"], store=store)
    # the symbol-relevant decision ranks above the merely-recent one
    assert out.index("dec-100") < out.index("dec-200")


async def test_semantic_search_hook_contributes(store: SessionStore) -> None:
    async def fake_search(query: str) -> list[tuple[str, str, float]]:
        return [("dec-sem", "surfaced by mem0", 0.9)]

    out = await recall_context("axon", store=store, semantic_search=fake_search)
    assert "dec-sem" in out and "surfaced by mem0" in out


async def test_semantic_search_failure_is_graceful(store: SessionStore) -> None:
    await store.save_decision(_decision(id="dec-001", summary="alpha"))

    async def boom(query: str) -> list[tuple[str, str, float]]:
        raise RuntimeError("mem0 down")

    out = await recall_context("axon", store=store, semantic_search=boom)
    assert "dec-001" in out  # recall still works despite the failing source


async def test_token_budget_truncates(store: SessionStore) -> None:
    for i in range(20):
        await store.save_decision(
            _decision(id=f"dec-{i:03d}", summary="x" * 60, validation_score=float(i % 5))
        )
    full = await recall_context("axon", store=store, token_budget=10_000)
    tight = await recall_context("axon", store=store, token_budget=60)
    assert len(tight) < len(full)
    assert tight.count("\n- ") < full.count("\n- ")


async def test_decision_from_repo_and_symbol_is_deduped(store: SessionStore) -> None:
    await store.save_decision(
        _decision(id="dec-001", summary="alpha", symbols=["pkg.S"])
    )
    out = await recall_context("axon", symbols=["pkg.S"], store=store)
    assert out.count("dec-001") == 1
