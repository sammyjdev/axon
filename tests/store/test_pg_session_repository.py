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


async def test_large_summary_and_note_roundtrip_and_dedup(pg_dsn) -> None:
    # Regression #111: the natural-key UNIQUE indexes were btree over the full
    # text columns, so any summary/body above ~2.6KB failed to insert (btree
    # caps index rows at 2704 bytes). The indexes now hash the text via md5().
    # High-entropy content, or TOAST compression shrinks it under the cap and
    # the regression never trips.
    import hashlib

    from axon.store.pg_session_repository import PostgresSessionRepository

    def incompressible(seed: str, chars: int) -> str:
        out: list[str] = []
        i = 0
        while sum(len(c) for c in out) < chars:
            out.append(hashlib.sha256(f"{seed}{i}".encode()).hexdigest())
            i += 1
        return "".join(out)[:chars]

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        big_summary = incompressible("summary", 9000)
        mem = SessionMemory(project="p111", summary=big_summary, raw_turns=0)
        i1 = await repo.save_session_memory(mem)
        i2 = await repo.save_session_memory(mem)  # natural-key dedup still holds
        assert i1 == i2
        mems = await repo.get_session_memories("p111", limit=1)
        assert mems[0].summary == big_summary

        big_body = incompressible("body", 9000)
        note = SessionNote(project="p111", body=big_body)
        n1 = await repo.save_note(note)
        n2 = await repo.save_note(note)
        assert n1 == n2
        assert (await repo.get_notes("p111"))[0].body == big_body
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
