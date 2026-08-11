"""Wave C Task 7: the store cannot read outside its own database.

Isolation here is the connection, not a permission check: a client's lessons
live in the client's database, so a second store pointed at a different,
empty database must never see them - and the DSN that picks that database
must come from config with no fallback, since a silent fallback (to another
store's DSN, or to a hardcoded default) is exactly what would let two clients
land on the same database by accident.
"""
from __future__ import annotations

import asyncio

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.models.lesson import LessonRecord  # noqa: E402
from axon.store.lessons import LessonStore, resolve_lessons_dsn  # noqa: E402


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg


@pytest.fixture(scope="module")
def main_dsn(pg_container):
    return pg_container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture(scope="module")
def other_dsn(pg_container, main_dsn):
    """A second, genuinely empty database on the same server."""

    async def _create() -> None:
        con = await asyncpg.connect(main_dsn)
        try:
            await con.execute("CREATE DATABASE isolated")
        finally:
            await con.close()

    asyncio.run(_create())
    return f"{main_dsn.rsplit('/', 1)[0]}/isolated"


def _lesson(**over: object) -> LessonRecord:
    base = {
        "kind": "agent-error",
        "triggers": ["shell-script", "bash"],
        "mistake": "used mapfile, which bash 3.2 does not have",
        "tell": "line 3: mapfile: command not found",
        "fix": "read -r -d '' into an array",
        "source": "forge/agent-errors",
    }
    return LessonRecord(**{**base, **over})  # type: ignore[arg-type]


async def test_a_store_on_an_empty_database_cannot_see_a_lesson_in_another(
    main_dsn, other_dsn
):
    main_store = LessonStore(dsn=main_dsn)
    await main_store.init()
    lesson_id = await main_store.insert(_lesson())

    other_store = LessonStore(dsn=other_dsn)
    await other_store.init()

    assert await other_store.get(lesson_id) is None


def test_resolve_lessons_dsn_raises_when_unset(monkeypatch):
    monkeypatch.delenv("AXON_LESSONS_PG_URL", raising=False)

    with pytest.raises(RuntimeError):
        resolve_lessons_dsn()


def test_resolve_lessons_dsn_does_not_fall_back_to_the_shared_pg_url(monkeypatch):
    """A missing lessons DSN must refuse, not quietly reuse AXON_PG_URL."""
    monkeypatch.delenv("AXON_LESSONS_PG_URL", raising=False)
    monkeypatch.setenv("AXON_PG_URL", "postgresql://axon:axon@localhost:5433/axon")

    with pytest.raises(RuntimeError):
        resolve_lessons_dsn()


def test_resolve_lessons_dsn_returns_the_configured_value(monkeypatch):
    monkeypatch.setenv("AXON_LESSONS_PG_URL", "postgresql://x:y@host/db")

    assert resolve_lessons_dsn() == "postgresql://x:y@host/db"
