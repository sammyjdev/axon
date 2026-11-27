"""The harness session id: the key of one `session_memory` row (#187)."""
from __future__ import annotations

from contextvars import ContextVar

#: Set by the session and compact hooks for the duration of one hook. It travels
#: here rather than in the signatures between the hook and the save.
current_hook_session_id: ContextVar[str | None] = ContextVar(
    "current_hook_session_id", default=None
)


def normalize_session_id(raw: object) -> str | None:
    """The stripped id when it is a string of 1-200 characters, else None."""
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped if 1 <= len(stripped) <= 200 else None
