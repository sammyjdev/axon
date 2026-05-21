from __future__ import annotations

VALID_CONTEXTS = ("personal", "career", "knowledge", "saas", "work")
DEFAULT_SEARCH_CONTEXTS = tuple(ctx for ctx in VALID_CONTEXTS if ctx != "work")
PROTECTED_CONTEXTS = {"work"}


def normalize_context(ctx: str | None) -> str | None:
    if ctx is None:
        return None
    normalized = ctx.strip().lower()
    return normalized or None
