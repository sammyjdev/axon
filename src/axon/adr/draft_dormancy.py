"""Shared aging rule for draft pools (dec-111; reused by the Lesson pool).

The ADR draft pool and the Lesson draft pool (``Lesson:`` trailer) are
both Markdown+frontmatter files on disk that age out identically: after
``dormancy_days`` without action, a draft is marked dormant rather than
deleted. That's the one piece genuinely shared between the two pools —
what fields a draft carries, and how it's rendered, differ enough that
the rest isn't worth sharing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar

DEFAULT_DORMANCY_DAYS = 30


class DormancyRecord(Protocol):
    created_at: datetime
    dormant: bool


_R = TypeVar("_R", bound=DormancyRecord)


def due_for_dormancy(
    records: list[_R], *, dormancy_days: int = DEFAULT_DORMANCY_DAYS
) -> list[_R]:
    """Return the subset of ``records`` old enough to become dormant.

    Pure selection only — persisting the transition is left to the
    caller, since the write mechanics (frontmatter shape) differ per
    pool.
    """
    threshold = datetime.now(UTC) - timedelta(days=dormancy_days)
    return [r for r in records if not r.dormant and r.created_at < threshold]
