"""
Testes unitários dos stores — T-034.
graph_store e session_store usam Testcontainers.
collections.py é testado sem infra (lógica pura).
vector_store requer Qdrant real — testa apenas a lógica de agrupamento/batch.
"""

from collections.abc import AsyncGenerator

import pytest

from axon.store.collections import get_search_collections
from axon.store.session_store import ADR, CodeChange, SessionMemory, SessionStore

# ── collections.py ─────────────────────────────────────────────────────────────


class TestGetSearchCollections:
    def test_no_ctx_excludes_work(self) -> None:
        result = get_search_collections(None)
        assert "work" not in result
        assert set(result) == {"personal", "career", "knowledge", "saas"}

    def test_explicit_work_ctx_returns_only_work(self) -> None:
        result = get_search_collections("work")
        assert result == ["work"]

    def test_personal_ctx_excludes_work(self) -> None:
        result = get_search_collections("personal")
        assert result == ["personal"]

    def test_explicit_non_work_ctx_returns_only_that_context(self) -> None:
        assert get_search_collections("knowledge") == ["knowledge"]
        assert get_search_collections("career") == ["career"]
        assert get_search_collections("saas") == ["saas"]

    def test_empty_string_ctx_excludes_work(self) -> None:
        result = get_search_collections("")
        assert "work" not in result


# ── session_store.py ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def pg_dsn():
    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def session_store(pg_dsn, tmp_path, monkeypatch) -> AsyncGenerator[SessionStore, None]:
    # Isolated per-test Postgres store via a fresh container + TRUNCATE.
    import asyncpg

    monkeypatch.setenv("AXON_PG_URL", pg_dsn)
    store = SessionStore(db_path=tmp_path / "test.db")
    await store.init()
    await store._decisions()
    await store._sessions()
    con = await asyncpg.connect(pg_dsn)
    await con.execute("TRUNCATE decisions, adr, sessions, session_memory, session_note, code_change")
    await con.close()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestSessionStore:
    async def test_save_and_get_adr(self, session_store) -> None:
        adr = ADR(
            project="aerus-rpg",
            title="Usar event sourcing para combate",
            context="Precisamos replay de estados",
            decision="Event sourcing com Redis Streams",
            rationale="Facilita undo/redo e replay",
        )
        adr_id = await session_store.save_adr(adr)
        assert adr_id > 0

        adrs = await session_store.get_adrs("aerus-rpg")
        assert len(adrs) == 1
        assert adrs[0].title == "Usar event sourcing para combate"
        assert adrs[0].project == "aerus-rpg"

    async def test_get_adrs_empty_project(self, session_store) -> None:
        adrs = await session_store.get_adrs("nonexistent")
        assert adrs == []

    async def test_save_and_get_session_memory(self, session_store) -> None:
        mem = SessionMemory(
            project="aerus-rpg",
            summary="Implementamos o sistema de combate com turnos.",
            raw_turns=15,
        )
        mem_id = await session_store.save_session_memory(mem)
        assert mem_id > 0

        mems = await session_store.get_session_memories("aerus-rpg")
        assert len(mems) == 1
        assert mems[0].raw_turns == 15
        assert "combate" in mems[0].summary

    async def test_session_memory_respects_limit(self, session_store) -> None:
        for i in range(5):
            await session_store.save_session_memory(
                SessionMemory(project="p", summary=f"session {i}", raw_turns=i)
            )
        mems = await session_store.get_session_memories("p", limit=3)
        assert len(mems) == 3

    async def test_save_and_get_code_change(self, session_store) -> None:
        change = CodeChange(
            commit_hash="abc123",
            file_path="src/combat/Engine.java",
            diff_summary="Added turn-based combat loop",
            why="feat: implement combat engine",
        )
        await session_store.save_code_change(change)

        changes = await session_store.get_recent_changes("src/combat/Engine.java")
        assert len(changes) == 1
        assert changes[0].commit_hash == "abc123"

    async def test_code_change_upsert_on_duplicate_key(self, session_store) -> None:
        change = CodeChange(
            commit_hash="abc123",
            file_path="src/Engine.java",
            diff_summary="v1",
        )
        await session_store.save_code_change(change)

        change2 = CodeChange(
            commit_hash="abc123",
            file_path="src/Engine.java",
            diff_summary="v2 updated",
        )
        await session_store.save_code_change(change2)

        changes = await session_store.get_recent_changes("src/Engine.java")
        assert len(changes) == 1
        assert changes[0].diff_summary == "v2 updated"

    async def test_get_recent_changes_empty(self, session_store) -> None:
        changes = await session_store.get_recent_changes("nonexistent.java")
        assert changes == []
