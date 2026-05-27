from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from axon.core.decision import Decision
from axon.mcp import server
from axon.observability.trace_store import TraceStore
from axon.store.session_store import SessionStore


@pytest.fixture
def trace_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TraceStore:
    ts = TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "trace"))
    monkeypatch.setattr(server, "_TRACE_STORE", ts)
    return ts


@pytest.fixture
async def session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    monkeypatch.setattr(server, "_get_session_store", lambda: s)
    yield s
    await s.close()


async def _populate(store: SessionStore, scores: list[float]) -> None:
    for i, s in enumerate(scores):
        await store.save_decision(
            Decision(
                id=f"dec-{i:03d}",
                timestamp=datetime.now(UTC),
                agent="claude-code",
                repo="axon",
                summary=f"sum {i}",
                validation_score=s,
                judged=True,
                status="draft",
            )
        )


@pytest.mark.asyncio
async def test_axon_validation_stats_returns_aggregated_json(
    session: SessionStore,
    trace_store: TraceStore,
) -> None:
    await _populate(session, [5.0, 4.0, 3.5, 3.0, 1.0])

    response = await server.axon_validation_stats(repo="axon", threshold=3.5)

    data = json.loads(response)
    assert data["n_total"] == 5
    assert data["n_scored"] == 5
    assert data["n_passed"] == 3
    assert data["pass_rate"] == pytest.approx(0.6)
    assert data["threshold"] == 3.5


@pytest.mark.asyncio
async def test_axon_validation_stats_emits_trace_with_pass_rate(
    session: SessionStore,
    trace_store: TraceStore,
) -> None:
    await _populate(session, [5.0, 1.0])

    await server.axon_validation_stats(repo="axon", threshold=3.5)

    records = trace_store.load_all()
    stages = [r.stage for r in records]
    assert "invoke" in stages
    assert "output" in stages
    val_records = [r for r in records if r.stage == "validation_result"]
    assert val_records, "expected a validation_result stage"
    payload = val_records[0].payload
    assert payload["n_total"] == 2
    assert payload["pass_rate"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_axon_validation_stats_when_empty_returns_no_decisions(
    session: SessionStore,
    trace_store: TraceStore,
) -> None:
    response = await server.axon_validation_stats(repo="axon")
    assert "no decisions" in response.lower()


@pytest.mark.asyncio
async def test_axon_validation_stats_repo_none_aggregates_workspace(
    session: SessionStore,
    trace_store: TraceStore,
) -> None:
    for i, (repo, score) in enumerate(
        [("axon", 5.0), ("other", 4.0), ("axon", 1.0)]
    ):
        await session.save_decision(
            Decision(
                id=f"dec-{i:03d}",
                timestamp=datetime.now(UTC),
                agent="claude-code",
                repo=repo,
                summary=f"s{i}",
                validation_score=score,
                judged=True,
                status="draft",
            )
        )

    response = await server.axon_validation_stats(repo=None, threshold=3.5)
    data = json.loads(response)
    # Aggregates across BOTH repos, not the cwd
    assert data["n_total"] == 3
    assert data["n_scored"] == 3
    assert data["n_passed"] == 2
