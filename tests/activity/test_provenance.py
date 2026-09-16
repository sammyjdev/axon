from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer

from axon.activity.repository import PostgresActivityRepository
from axon.mcp import server
from axon.store.session_store import SessionStore


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"  # noqa: S106
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")

@pytest.fixture
async def pg_pool(pg_dsn):
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        yield pool
    finally:
        await pool.close()

@pytest.fixture
async def pg_setup(pg_dsn, pg_pool):
    async with pg_pool.acquire() as con:
        await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    
    repo = PostgresActivityRepository(pg_dsn)
    await repo.ensure_schema()
    await repo.close()
    return pg_dsn

@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()

@pytest.fixture(autouse=True)
def _patch_env(store: SessionStore, pg_setup: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_get_session_store", lambda: store)
    # Patch _RUNTIME entirely because it is a frozen dataclass
    import dataclasses
    new_runtime = dataclasses.replace(server._RUNTIME, pg_url=pg_setup)
    monkeypatch.setattr(server, "_RUNTIME", new_runtime)

async def test_explicit_activity_reference_resolves_from_memory_record(pg_setup: str) -> None:
    out = await server.axon_capture_event(
        "manual_note", 
        {"repo": "x", "text": "hi"},
        activity_session_id="s1", 
        activity_turn_id="t1"
    )
    assert "captured manual_note for x" in out
    
    # Check DB
    repo = PostgresActivityRepository(pg_setup)
    try:
        store = server._get_session_store()
        notes = await store.get_notes("x")
        note_id = notes[0].id
        
        links = await repo.get_evidence_links(target_type="session_note", target_id=str(note_id))
        assert len(links) == 1
        assert links[0]["session_id"] == "s1"
        assert links[0]["turn_id"] == "t1"
        assert links[0]["relation"] == "record-supported"
    finally:
        await repo.close()

async def test_recalled_context_is_recorded_as_delivered_not_used(pg_setup: str) -> None:
    out = await server.axon_session_start(
        agent="claude-code", 
        repo="x",
        activity_session_id="s2"
    )
    assert "session:" in out
    session_id = out.split("session:")[1].split()[0]
    
    # Check DB
    repo = PostgresActivityRepository(pg_setup)
    try:
        links = await repo.get_evidence_links(target_type="context_delivery", target_id=session_id)
        assert len(links) == 1
        assert links[0]["relation"] == "context-delivered"
    finally:
        await repo.close()

async def test_existing_memory_without_provenance_is_still_valid(pg_setup: str) -> None:
    out1 = await server.axon_capture_event("manual_note", {"repo": "x", "text": "hi"})
    assert "captured manual_note for x" in out1
    
    out2 = await server.axon_session_start(agent="claude-code", repo="x")
    assert "session:" in out2
    session_id = out2.split("session:")[1].split()[0]
    
    # Check DB
    repo = PostgresActivityRepository(pg_setup)
    try:
        store = server._get_session_store()
        notes = await store.get_notes("x")
        note_id = notes[0].id
        
        links1 = await repo.get_evidence_links(target_type="session_note", target_id=str(note_id))
        assert len(links1) == 0
        
        links2 = await repo.get_evidence_links(target_type="context_delivery", target_id=session_id)
        assert len(links2) == 0
    finally:
        await repo.close()

async def test_shared_workspace_and_timestamp_do_not_create_provenance_link(pg_setup: str) -> None:
    out1 = await server.axon_capture_event("manual_note", {"repo": "shared", "text": "hi"})
    out2 = await server.axon_session_start(agent="claude-code", repo="shared")
    assert "captured manual_note for shared" in out1
    assert "session:" in out2
    session_id = out2.split("session:")[1].split()[0]
    
    # Check DB
    repo = PostgresActivityRepository(pg_setup)
    try:
        store = server._get_session_store()
        notes = await store.get_notes("shared")
        note_id = notes[0].id
        
        links1 = await repo.get_evidence_links(target_type="session_note", target_id=str(note_id))
        assert len(links1) == 0
        
        links2 = await repo.get_evidence_links(target_type="context_delivery", target_id=session_id)
        assert len(links2) == 0
    finally:
        await repo.close()
