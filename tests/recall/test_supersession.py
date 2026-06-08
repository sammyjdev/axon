"""Soft supersession in recall ranking (dec-115).

A stale decision must be demoted but never dropped (lossless), the default
ranking must be untouched when the flag is off, and unrelated edits to a shared
file must not be mistaken for supersession.
"""

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


def _high_sim(_a: str, _b: str) -> float:
    return 0.95


def _low_sim(_a: str, _b: str) -> float:
    return 0.10


def _mid_sim(_a: str, _b: str) -> float:
    # Topically similar (same feature area) but NOT a near-duplicate.
    return 0.88


async def _seed_supersession_pair(store: SessionStore) -> None:
    """An old decision and a newer one that revises it, same scope."""
    await store.save_decision(
        _decision(
            id="dec-100",
            summary="graph backend is neo4j",
            timestamp=_NOW - timedelta(days=10),
            files=[Path("src/graph.py")],
        )
    )
    await store.save_decision(
        _decision(
            id="dec-200",
            summary="graph backend dropped neo4j for qdrant",
            timestamp=_NOW,
            files=[Path("src/graph.py")],
        )
    )


async def test_flag_off_does_not_demote(store: SessionStore) -> None:
    await _seed_supersession_pair(store)
    # Even with a similarity seam present, the flag being off means the legacy
    # ranking is preserved: the stale decision keeps its full (unpenalised) rank.
    ranks = await _ranks(store, similarity=_high_sim)
    assert ranks["dec-100"] > 0.05


async def test_supersession_demotes_old_but_keeps_it(store: SessionStore) -> None:
    await _seed_supersession_pair(store)
    out = await recall_context(
        "axon",
        store=store,
        enable_supersession=True,
        similarity=_high_sim,
    )
    # Lossless: the stale decision is still present...
    assert "dec-100" in out
    # ...but ranked below its successor.
    assert out.index("dec-200") < out.index("dec-100")


async def test_low_similarity_is_not_supersession(store: SessionStore) -> None:
    # Same file, but the summaries are about different subjects.
    await store.save_decision(
        _decision(
            id="dec-110",
            summary="add logging to graph module",
            timestamp=_NOW - timedelta(days=10),
            files=[Path("src/graph.py")],
        )
    )
    await store.save_decision(
        _decision(
            id="dec-120",
            summary="rename a helper in graph module",
            timestamp=_NOW,
            files=[Path("src/graph.py")],
        )
    )
    ranks = await _ranks(
        store, enable_supersession=True, similarity=_low_sim
    )
    # Neither is penalised: shared file is not enough without semantic agreement.
    assert ranks["dec-110"] > 0.05 and ranks["dec-120"] > 0.05


async def test_mid_similarity_additive_is_not_supersession(store: SessionStore) -> None:
    # Same file, topically similar (0.88) but ADDITIVE: the newer decision adds a
    # feature, it does not revise the older. No revision verb, not a near-duplicate
    # -> must NOT be supersession. This is the real-world false positive a flat
    # 0.82 cosine threshold produced (PitStopOS dec-064 -> dec-076, etc.).
    await store.save_decision(
        _decision(
            id="dec-210",
            summary="add commission calculation per service order",
            timestamp=_NOW - timedelta(days=10),
            files=[Path("src/repository.py")],
        )
    )
    await store.save_decision(
        _decision(
            id="dec-220",
            summary="add cash period view with net commission",
            timestamp=_NOW,
            files=[Path("src/repository.py")],
        )
    )
    ranks = await _ranks(store, enable_supersession=True, similarity=_mid_sim)
    assert ranks["dec-210"] > 0.05 and ranks["dec-220"] > 0.05


async def test_revision_verb_rescues_mid_similarity(store: SessionStore) -> None:
    # Mid similarity (0.88, below the near-duplicate cut) but the newer summary
    # carries a revision verb -> it IS a revision, so the older is superseded.
    await store.save_decision(
        _decision(
            id="dec-310",
            summary="graph backend uses the neo4j driver",
            timestamp=_NOW - timedelta(days=10),
            files=[Path("src/graph.py")],
        )
    )
    await store.save_decision(
        _decision(
            id="dec-320",
            summary="drop neo4j from the graph backend",
            timestamp=_NOW,
            files=[Path("src/graph.py")],
        )
    )
    ranks = await _ranks(store, enable_supersession=True, similarity=_mid_sim)
    assert ranks["dec-310"] < 0.05 < ranks["dec-320"]


async def test_revision_verb_recognized_in_portuguese(store: SessionStore) -> None:
    # The verb signal is bilingual: AXON's own decisions are PT/EN.
    await store.save_decision(
        _decision(
            id="dec-330",
            summary="backend de grafo usa o driver neo4j",
            timestamp=_NOW - timedelta(days=10),
            files=[Path("src/graph.py")],
        )
    )
    await store.save_decision(
        _decision(
            id="dec-340",
            summary="substitui o neo4j por qdrant no backend de grafo",
            timestamp=_NOW,
            files=[Path("src/graph.py")],
        )
    )
    ranks = await _ranks(store, enable_supersession=True, similarity=_mid_sim)
    assert ranks["dec-330"] < 0.05 < ranks["dec-340"]


async def test_near_duplicate_without_verb_is_supersession(store: SessionStore) -> None:
    # No revision verb, but the summaries are near-identical (0.95) -> a reworded
    # restatement supersedes the original.
    await store.save_decision(
        _decision(
            id="dec-350",
            summary="compression strategy enabled for every turn",
            timestamp=_NOW - timedelta(days=10),
            files=[Path("src/recall/strategy.py")],
        )
    )
    await store.save_decision(
        _decision(
            id="dec-360",
            summary="compression strategy enabled on every turn",
            timestamp=_NOW,
            files=[Path("src/recall/strategy.py")],
        )
    )
    ranks = await _ranks(store, enable_supersession=True, similarity=_high_sim)
    assert ranks["dec-350"] < 0.05 < ranks["dec-360"]


async def test_disjoint_scope_is_not_supersession(store: SessionStore) -> None:
    await store.save_decision(
        _decision(
            id="dec-130",
            summary="graph backend is neo4j",
            timestamp=_NOW - timedelta(days=10),
            files=[Path("src/graph.py")],
        )
    )
    await store.save_decision(
        _decision(
            id="dec-140",
            summary="graph backend is neo4j",  # identical text, different file
            timestamp=_NOW,
            files=[Path("src/router.py")],
        )
    )
    ranks = await _ranks(
        store, enable_supersession=True, similarity=_high_sim
    )
    assert ranks["dec-130"] > 0.05 and ranks["dec-140"] > 0.05


async def test_preexisting_status_superseded_is_demoted(store: SessionStore) -> None:
    await store.save_decision(_decision(id="dec-150", summary="active one"))
    await store.save_decision(
        _decision(id="dec-160", summary="retired one", status="superseded")
    )
    out = await recall_context(
        "axon", store=store, enable_supersession=True, similarity=_high_sim
    )
    # Still present (lossless) but demoted below the live decision.
    assert "dec-160" in out
    assert out.index("dec-150") < out.index("dec-160")


async def test_semantic_only_candidate_is_untouched(store: SessionStore) -> None:
    async def fake_search(_query: str) -> list[tuple[str, str, float]]:
        return [("dec-sem", "surfaced by mem0", 0.9)]

    out = await recall_context(
        "axon",
        store=store,
        semantic_search=fake_search,
        enable_supersession=True,
        similarity=_high_sim,
    )
    # A mem0-only hit has no backing Decision, so it is exempt and still shown.
    assert "dec-sem" in out


async def _ranks(
    store: SessionStore, **kwargs: Any
) -> dict[str, float]:
    """Parse the rendered '(rank X.XX)' values back out for assertions."""
    import re

    out = await recall_context("axon", store=store, token_budget=10_000, **kwargs)
    return {
        m.group(1): float(m.group(2))
        for m in re.finditer(r"(dec-[\w]+) \(rank ([\d.]+)\)", out)
    }
