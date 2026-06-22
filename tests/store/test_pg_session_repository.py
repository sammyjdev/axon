from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.session_store import CodeChange, SessionMemory, SessionNote  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_memory_note_return_ids_and_order(pg_dsn) -> None:
    from axon.store.pg_session_repository import PostgresSessionRepository

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        await repo.ensure_schema()  # idempotent
        i1 = await repo.save_session_memory(SessionMemory(project="p", summary="a", raw_turns=1))
        i2 = await repo.save_session_memory(SessionMemory(project="p", summary="b", raw_turns=2))
        assert isinstance(i1, int) and i2 > i1
        mems = await repo.get_session_memories("p", limit=3)
        assert [m.summary for m in mems][0] in {"a", "b"} and len(mems) == 2
        nid = await repo.save_note(SessionNote(project="p", body="n"))
        assert isinstance(nid, int) and nid >= 1
        assert len(await repo.get_notes("p")) == 1
    finally:
        await repo.close()


async def test_code_change_upsert_and_session_lifecycle(pg_dsn) -> None:
    from axon.store.pg_session_repository import PostgresSessionRepository

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE code_change")
            await con.execute("TRUNCATE sessions")
        cc = CodeChange(commit_hash="abc", file_path="f.py", diff_summary="d", why="w")
        await repo.save_code_change(cc)
        await repo.save_code_change(  # upsert
            CodeChange(commit_hash="abc", file_path="f.py", diff_summary="d2", why="w2")
        )
        recent = await repo.get_recent_changes("f.py")
        assert len(recent) == 1 and recent[0].diff_summary == "d2"
        await repo.save_session("s1", "manual", "axon", context_payload="ctx")
        assert await repo.end_session("s1") == "axon"
        assert await repo.end_session("missing") is None
    finally:
        await repo.close()
