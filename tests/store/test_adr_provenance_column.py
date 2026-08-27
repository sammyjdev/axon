from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("testcontainers.postgres")
import asyncpg  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.pg_decision_repository import PostgresDecisionRepository  # noqa: E402
from axon.store.session_store import ADR  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"  # noqa: S106
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_ensure_schema_retrofits_provenance_onto_a_preexisting_table(pg_dsn: str) -> None:
    # Setup legacy table without provenance column
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("DROP TABLE IF EXISTS adr CASCADE")
        await con.execute(
            """
            CREATE TABLE adr (
                id         bigserial PRIMARY KEY,
                project    text NOT NULL,
                title      text NOT NULL,
                context    text NOT NULL,
                decision   text NOT NULL,
                rationale  text NOT NULL,
                created_at timestamptz NOT NULL
            )
            """
        )
        await con.execute(
            "INSERT INTO adr (project, title, context, decision, rationale, created_at)"
            " VALUES ('legacy_proj', 'Legacy Title', 'ctx', 'dec', 'rat', NOW())"
        )
    finally:
        await con.close()

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()

        # Check information_schema for provenance column
        check_con = await asyncpg.connect(pg_dsn)
        try:
            col_exists = await check_con.fetchval(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'adr' AND column_name = 'provenance'"
            )
            assert col_exists is not None

            prov_val = await check_con.fetchval(
                "SELECT provenance FROM adr WHERE project = 'legacy_proj'"
            )
            assert prov_val == "llm-inferred"
        finally:
            await check_con.close()

        # Idempotence check
        await repo.ensure_schema()
    finally:
        await repo.close()


async def test_provenance_round_trips_through_save_and_get(pg_dsn: str) -> None:
    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        human_adr = ADR(
            project="t_round_trip",
            title="Human Title",
            context="ctx",
            decision="dec",
            rationale="rat",
            provenance="human",
        )
        default_adr = ADR(
            project="t_round_trip",
            title="Default Title",
            context="ctx",
            decision="dec",
            rationale="rat",
        )
        await repo.save_adr(human_adr)
        await repo.save_adr(default_adr)

        adrs = await repo.get_adrs("t_round_trip")
        by_title = {a.title: a for a in adrs}

        assert by_title["Human Title"].provenance == "human"
        assert by_title["Default Title"].provenance == "llm-inferred"
    finally:
        await repo.close()


async def test_conflicting_insert_still_returns_the_existing_id(pg_dsn: str) -> None:
    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        now = datetime.now(UTC)
        adr_item = ADR(
            project="conflict_proj",
            title="Duplicate Title",
            context="ctx",
            decision="dec",
            rationale="rat",
            created_at=now,
        )
        id1 = await repo.save_adr(adr_item)
        id2 = await repo.save_adr(adr_item)

        assert id1 != 0
        assert id1 == id2
    finally:
        await repo.close()
