from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from axon.core.decision import Decision
from axon.store.session_store import SessionStore
from axon.validation.aggregate import pass_rate


def _decision(
    *, id: str, repo: str, score: float, judged: bool | None = None
) -> Decision:
    return Decision(
        id=id,
        timestamp=datetime.now(UTC),
        agent="claude-code",
        repo=repo,
        summary=f"sum {id}",
        validation_score=score,
        judged=judged if judged is not None else (score > 0.0),
        status="draft",
    )


@pytest.mark.asyncio
async def test_pass_rate_returns_none_when_no_decisions(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    try:
        result = await pass_rate(store=store)
        assert result is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_pass_rate_with_threshold_3_5(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    try:
        scores = [5.0, 4.0, 4.0, 3.5, 3.0, 3.0, 2.0, 1.0]
        for i, s in enumerate(scores):
            await store.save_decision(
                _decision(id=f"dec-{i:03d}", repo="axon", score=s)
            )

        result = await pass_rate(store=store, threshold=3.5)

        assert result is not None
        assert result.threshold == 3.5
        assert result.n_total == 8
        assert result.n_scored == 8
        assert result.n_passed == 4  # >= 3.5
        assert result.pass_rate == pytest.approx(0.5)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_pass_rate_excludes_unscored_decisions(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    try:
        await store.save_decision(_decision(id="dec-001", repo="axon", score=4.0))
        await store.save_decision(_decision(id="dec-002", repo="axon", score=0.0))
        await store.save_decision(_decision(id="dec-003", repo="axon", score=0.0))

        result = await pass_rate(store=store, threshold=3.5)

        assert result is not None
        assert result.n_total == 3
        assert result.n_scored == 1
        assert result.n_passed == 1
        assert result.pass_rate == pytest.approx(1.0)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_pass_rate_rejects_non_positive_threshold(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    try:
        with pytest.raises(ValueError):
            await pass_rate(store=store, threshold=0.0)
        with pytest.raises(ValueError):
            await pass_rate(store=store, threshold=-1.0)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_pass_rate_counts_judged_zero_score_as_scored(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    try:
        # A judge can legitimately rate a decision 0.0; the new `judged` flag
        # makes that distinguishable from unscored drafts.
        await store.save_decision(
            _decision(id="dec-001", repo="axon", score=0.0, judged=True)
        )
        await store.save_decision(
            _decision(id="dec-002", repo="axon", score=4.0, judged=True)
        )
        await store.save_decision(  # unscored draft
            _decision(id="dec-003", repo="axon", score=0.0, judged=False)
        )

        result = await pass_rate(store=store, threshold=3.5)
        assert result is not None
        assert result.n_total == 3
        assert result.n_scored == 2  # judged decisions only
        assert result.n_passed == 1  # only dec-002 passes the threshold
        assert result.pass_rate == pytest.approx(0.5)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_pass_rate_filterable_by_repo(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    try:
        await store.save_decision(_decision(id="dec-001", repo="axon", score=5.0))
        await store.save_decision(_decision(id="dec-002", repo="axon", score=1.0))
        await store.save_decision(_decision(id="dec-003", repo="other", score=5.0))

        result = await pass_rate(store=store, repo="axon", threshold=3.5)

        assert result is not None
        assert result.n_total == 2
        assert result.n_scored == 2
        assert result.n_passed == 1
    finally:
        await store.close()
