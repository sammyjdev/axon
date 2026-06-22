# tests/scripts/test_migrate_sessions.py
from __future__ import annotations


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
