from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.core.decision import Decision  # noqa: E402
from axon.store.session_store import ADR  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


def _dec(did, repo="axon", symbols=("sym1",), git_hash=None, judged=False, score=0.0):
    return Decision(
        id=did, timestamp=datetime(2026, 1, 1, tzinfo=UTC), agent="manual", repo=repo,
        symbols=list(symbols), summary="s", git_hash=git_hash, judged=judged,
        validation_score=score,
    )


async def test_decision_upsert_and_json_queries(pg_dsn) -> None:
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        await repo.ensure_schema()  # idempotent
        await repo.save_decision(
            _dec("dec-001", symbols=["alpha"], git_hash="abc", judged=True, score=3.5)
        )
        await repo.save_decision(_dec("dec-001", symbols=["alpha", "beta"]))  # upsert same id
        await repo.save_decision(_dec("dec-002", repo="other", symbols=["gamma"]))
        by_sym = await repo.find_decisions_by_symbol("beta")
        assert [d.id for d in by_sym] == ["dec-001"]
        by_repo = await repo.find_decisions_by_repo("axon")
        assert [d.id for d in by_repo] == ["dec-001"]
        assert await repo.next_decision_id() == "dec-003"  # COUNT=2 -> dec-003
        all_d = await repo.all_decisions()
        assert {d.id for d in all_d} == {"dec-001", "dec-002"}
    finally:
        await repo.close()


async def test_next_decision_id_non_contiguous_gap(pg_dsn) -> None:
    """Regression: next_decision_id must return max(id)+1, not count+1.

    With dec-001 and dec-003 (gap at dec-002, count=2), count+1 would yield
    dec-003 — an existing id, causing a collision. max+1 must yield dec-004.
    """
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE decisions")
        await repo.save_decision(_dec("dec-001"))
        await repo.save_decision(_dec("dec-003"))
        result = await repo.next_decision_id()
        assert result == "dec-004", (
            f"expected dec-004 (max=3, +1), got {result!r}; "
            "count+1 would return dec-003, colliding with existing id"
        )
    finally:
        await repo.close()


async def test_judged_roundtrip_and_git_hash(pg_dsn) -> None:
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE decisions")
        await repo.save_decision(_dec("dec-010", git_hash="deadbeef", judged=True, score=4.0))
        found = await repo.find_decision_by_git_hash("deadbeef", repo="axon")
        assert found is not None and found.judged is True and found.validation_score == 4.0
        assert await repo.find_decision_by_git_hash("deadbeef", repo="nope") is None
    finally:
        await repo.close()


async def test_every_decision_recorded_for_a_commit_is_returned(pg_dsn) -> None:
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE decisions")
        await repo.save_decision(_dec("dec-020", git_hash="cafe01"))
        await repo.save_decision(_dec("dec-021", git_hash="cafe01"))
        await repo.save_decision(_dec("dec-022", git_hash="other"))

        found = await repo.find_decisions_by_git_hash("cafe01")

        assert sorted(d.id for d in found) == ["dec-020", "dec-021"]
    finally:
        await repo.close()


async def test_adr_insert_returns_id_and_get(pg_dsn) -> None:
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE adr")
        adr = ADR(project="p", title="t", context="c", decision="d", rationale="r",
                  created_at=datetime(2026, 1, 1, tzinfo=UTC))
        new_id = await repo.save_adr(adr)
        assert isinstance(new_id, int) and new_id >= 1
        got = await repo.get_adrs("p")
        assert len(got) == 1 and got[0].title == "t"
    finally:
        await repo.close()


async def test_adr_save_inner_is_idempotent(pg_dsn) -> None:
    """Re-inserting the same ADR (same project/title/created_at) must NOT duplicate rows."""
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE adr")
        adr = ADR(project="proj", title="dup-title", context="c", decision="d",
                  rationale="r", created_at=datetime(2026, 6, 1, tzinfo=UTC))
        id1 = await repo.save_adr_inner(adr)
        id2 = await repo.save_adr_inner(adr)  # identical natural key - must not insert again
        assert id1 == id2, "re-insert must return the same id, not a new row"
        rows = await repo.get_adrs("proj", limit=100)
        assert len(rows) == 1, f"expected 1 ADR row, got {len(rows)}"
    finally:
        await repo.close()


def _injectable(
    did,
    *,
    repo="axon",
    status="active",
    files=(),
    summary="s",
    when=None,
):
    return Decision(
        id=did,
        timestamp=when or datetime(2026, 3, 1, tzinfo=UTC),
        agent="manual",
        repo=repo,
        files=[Path(path) for path in files],
        summary=summary,
        status=status,
    )


async def test_find_injectable_keeps_active_and_counts_drafts(pg_dsn) -> None:
    """CTX-01, CTX-02: only active rows are returned; drafts are a count."""
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE decisions")
        await repo.save_decision(_injectable("dec-101", summary="keep me"))
        await repo.save_decision(_injectable("dec-102", status="draft", summary="hidden draft"))
        await repo.save_decision(
            _injectable("dec-103", status="superseded", summary="old decision")
        )
        await repo.save_decision(
            _injectable("dec-104", status="deprecated", summary="retired decision")
        )
        await repo.save_decision(_injectable("dec-105", status="draft", summary="second draft"))
        await repo.save_decision(
            _injectable("dec-106", repo="other", summary="other repo")
        )

        found = await repo.find_injectable("axon")

        assert [d.id for d in found.active] == ["dec-101"]
        assert all(d.status == "active" for d in found.active)
        assert found.draft_count == 2
        assert "hidden draft" not in [d.summary for d in found.active]
    finally:
        await repo.close()


async def test_find_injectable_matches_the_file_exactly(pg_dsn) -> None:
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE decisions")
        await repo.save_decision(
            _injectable("dec-201", files=["src/a.py", "src/b.py"], summary="both")
        )
        await repo.save_decision(_injectable("dec-202", files=["src/b.py"], summary="only b"))
        await repo.save_decision(
            _injectable("dec-203", files=["src/a.py.bak"], summary="prefix lookalike")
        )
        await repo.save_decision(
            _injectable("dec-204", status="draft", files=["src/a.py"], summary="draft on a")
        )

        found = await repo.find_injectable("axon", file="src/a.py")

        assert [d.id for d in found.active] == ["dec-201"]
        assert found.draft_count == 1
        lookalike = await repo.find_injectable("axon", file=Path("src/a.py.bak"))
        assert [d.id for d in lookalike.active] == ["dec-203"]
    finally:
        await repo.close()


async def test_find_injectable_orders_by_recency(pg_dsn) -> None:
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE decisions")
        await repo.save_decision(
            _injectable("dec-301", when=datetime(2026, 1, 1, tzinfo=UTC), summary="older")
        )
        await repo.save_decision(
            _injectable("dec-302", when=datetime(2026, 6, 1, tzinfo=UTC), summary="newer")
        )

        found = await repo.find_injectable("axon")

        assert [d.id for d in found.active] == ["dec-302", "dec-301"]
    finally:
        await repo.close()
