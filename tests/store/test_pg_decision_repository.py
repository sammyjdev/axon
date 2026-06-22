from __future__ import annotations

from datetime import UTC, datetime

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
        await repo.save_decision(_dec("dec-001", symbols=["alpha"], git_hash="abc", judged=True, score=3.5))
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
