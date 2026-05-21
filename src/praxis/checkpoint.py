"""SQLite-backed checkpointing for the Praxis graph.

Wraps LangGraph's :class:`SqliteSaver` so a session's state survives a process
restart: the same database file reopened in a fresh process yields an identical
:class:`praxis.state.TaskState`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

DEFAULT_DB_PATH = ".praxis/praxis.sqlite"


def open_saver(db_path: str | Path = DEFAULT_DB_PATH) -> SqliteSaver:
    """Open (creating if needed) a file-backed :class:`SqliteSaver`.

    Use ``":memory:"`` for an ephemeral, in-process checkpointer.
    """
    raw = str(db_path)
    if raw != ":memory:":
        path = Path(raw).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = str(path)
    conn = sqlite3.connect(raw, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver
