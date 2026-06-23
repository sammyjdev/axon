# tests/scripts/test_migrate_sessions.py
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


class _FakeSrcRepo:
    def __init__(self, memories=None, notes=None, code_changes=None, sessions=None):
        self._memories = memories or []
        self._notes = notes or []
        self._code_changes = code_changes or []
        self._sessions = sessions or []

    async def all_memories(self):
        return self._memories

    async def all_notes(self):
        return self._notes

    async def all_code_changes(self):
        return self._code_changes

    async def all_sessions(self):
        return self._sessions


class _FakeDstRepo:
    def __init__(self):
        self.saved_memories = []
        self.saved_notes = []
        self.saved_code_changes = []
        self.saved_sessions = []

    async def save_session_memory(self, mem):
        self.saved_memories.append(mem)
        return len(self.saved_memories)

    async def save_note(self, note):
        self.saved_notes.append(note)
        return len(self.saved_notes)

    async def save_code_change_inner(self, change):
        self.saved_code_changes.append(change)

    async def save_session(self, session_id, agent, repo, *, context_payload=""):
        self.saved_sessions.append((session_id, agent, repo))


async def test_copy_sessions_counts_and_calls() -> None:
    from axon.store.session_store import CodeChange, SessionMemory, SessionNote
    from scripts.migrate_sessions import copy_sessions

    src = _FakeSrcRepo(
        memories=[SessionMemory(project="axon", summary="s", raw_turns=1)],
        notes=[SessionNote(project="axon", body="n"), SessionNote(project="axon", body="n2")],
        code_changes=[CodeChange(commit_hash="abc", file_path="f.py", diff_summary="d", why="w")],
        sessions=[{"id": "s1", "agent": "manual", "repo": "axon"}],
    )
    dst = _FakeDstRepo()

    counts = await copy_sessions(src, dst)

    assert counts == {"memories": 1, "notes": 2, "code_changes": 1, "sessions": 1}
    assert len(dst.saved_memories) == 1
    assert len(dst.saved_notes) == 2
    assert len(dst.saved_code_changes) == 1
    assert dst.saved_sessions == [("s1", "manual", "axon")]


# ---------------------------------------------------------------------------
# PG-backed dedup tests
# ---------------------------------------------------------------------------

async def test_copy_sessions_is_idempotent_on_rerun(pg_dsn) -> None:
    """Running copy_sessions twice must not create duplicate rows."""
    from axon.store.pg_session_repository import PostgresSessionRepository
    from scripts.migrate_sessions import copy_sessions

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        # Wipe tables for a clean slate
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE session_memory, session_note, code_change, sessions")

        m1 = SessionMemory(project="axon", summary="mem1", raw_turns=1)
        m2 = SessionMemory(project="axon", summary="mem2", raw_turns=2)
        n1 = SessionNote(project="axon", body="note1")
        n2 = SessionNote(project="axon", body="note2")
        cc = CodeChange(commit_hash="abc123", file_path="f.py", diff_summary="d", why="w")
        src = _FakeSrcRepo(
            memories=[m1, m2],
            notes=[n1, n2],
            code_changes=[cc],
            sessions=[{"id": "sess1", "agent": "manual", "repo": "axon"}],
        )

        await copy_sessions(src, repo)
        await copy_sessions(src, repo)  # second run - must not duplicate

        memories = await repo.all_memories()
        notes = await repo.all_notes()
        code_changes = await repo.all_code_changes()
        sessions = await repo.all_sessions()

        assert len(memories) == 2, f"Expected 2 memories, got {len(memories)}"
        assert len(notes) == 2, f"Expected 2 notes, got {len(notes)}"
        assert len(code_changes) == 1, f"Expected 1 code_change, got {len(code_changes)}"
        assert len(sessions) == 1, f"Expected 1 session, got {len(sessions)}"
    finally:
        await repo.close()


async def test_save_session_memory_dedups_identical_row(pg_dsn) -> None:
    """Saving the same SessionMemory twice returns the same id and keeps exactly 1 row."""
    from axon.store.pg_session_repository import PostgresSessionRepository

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE session_memory")

        mem = SessionMemory(project="p", summary="dedup-test", raw_turns=5)
        id1 = await repo.save_session_memory(mem)
        id2 = await repo.save_session_memory(mem)

        assert id1 == id2, f"Expected same id on dedup, got {id1} vs {id2}"
        all_mems = await repo.all_memories()
        assert len(all_mems) == 1, f"Expected 1 row after dedup, got {len(all_mems)}"

        # A different memory still inserts
        mem2 = SessionMemory(project="p", summary="different-summary", raw_turns=5)
        id3 = await repo.save_session_memory(mem2)
        assert id3 != id1
        assert len(await repo.all_memories()) == 2
    finally:
        await repo.close()


async def test_save_note_dedups_identical_row(pg_dsn) -> None:
    """Saving the same SessionNote twice returns the same id and keeps exactly 1 row."""
    from axon.store.pg_session_repository import PostgresSessionRepository

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE session_note")

        note = SessionNote(project="p", body="dedup-note")
        id1 = await repo.save_note(note)
        id2 = await repo.save_note(note)

        assert id1 == id2, f"Expected same id on dedup, got {id1} vs {id2}"
        all_notes = await repo.all_notes()
        assert len(all_notes) == 1, f"Expected 1 row after dedup, got {len(all_notes)}"

        # A different note still inserts
        note2 = SessionNote(project="p", body="different-note")
        id3 = await repo.save_note(note2)
        assert id3 != id1
        assert len(await repo.all_notes()) == 2
    finally:
        await repo.close()


def test_copy_sessions_docstring_states_idempotency_scope() -> None:
    """migrate_sessions docstring must describe HOW each type is idempotent (dedup basis)."""
    import scripts.migrate_sessions as mod

    doc = mod.__doc__ or ""
    keywords = {"ON CONFLICT", "natural key", "dedup"}
    assert any(kw in doc for kw in keywords), (
        f"Docstring must mention dedup basis (one of {keywords}), got: {doc!r}"
    )
