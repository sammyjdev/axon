"""Shared column definitions and row->model builder functions for the
Postgres session repository.

A single source of truth: any schema change (new column, rename, type tweak)
is made here.

Timestamp columns are timestamptz as of 0004 (#31), so asyncpg hands back
datetime objects directly - no fromisoformat parsing.
"""
from __future__ import annotations

from typing import Any

from axon.store.session_store import CodeChange, SessionMemory, SessionNote

# ── Column name tuples (INSERT order) ────────────────────────────────────────

SESSION_MEMORY_COLS: tuple[str, ...] = ("project", "summary", "raw_turns", "created_at")
SESSION_MEMORY_SELECT: tuple[str, ...] = ("id",) + SESSION_MEMORY_COLS

SESSION_NOTE_COLS: tuple[str, ...] = ("project", "body", "created_at")
SESSION_NOTE_SELECT: tuple[str, ...] = ("id",) + SESSION_NOTE_COLS

CODE_CHANGE_COLS: tuple[str, ...] = (
    "commit_hash", "file_path", "diff_summary", "why", "changed_at"
)

SESSIONS_COLS: tuple[str, ...] = (
    "id", "agent", "repo", "started_at", "ended_at", "context_payload"
)


# ── Row -> model converters ───────────────────────────────────────────────────

def row_to_session_memory(r: Any) -> SessionMemory:
    """Build a SessionMemory from any row/mapping with the expected column keys."""
    return SessionMemory(
        id=r["id"],
        project=r["project"],
        summary=r["summary"],
        raw_turns=r["raw_turns"],
        created_at=r["created_at"],
    )


def row_to_session_note(r: Any) -> SessionNote:
    """Build a SessionNote from any row/mapping with the expected column keys."""
    return SessionNote(
        id=r["id"],
        project=r["project"],
        body=r["body"],
        created_at=r["created_at"],
    )


def row_to_code_change(r: Any) -> CodeChange:
    """Build a CodeChange from any row/mapping with the expected column keys."""
    return CodeChange(
        commit_hash=r["commit_hash"],
        file_path=r["file_path"],
        diff_summary=r["diff_summary"],
        why=r["why"],
        changed_at=r["changed_at"],
    )
