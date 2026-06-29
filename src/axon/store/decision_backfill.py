"""dec-121 backfill: copy legacy SQLite decisions/ADRs into Postgres, resolving
the decision-id collision. See docs/superpowers/specs/2026-06-29-dec121-decision-backfill-design.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class DecRef:
    id: str
    git_hash: str  # "" when absent
    content_key: str


@dataclass(frozen=True)
class BackfillPlan:
    copy_legacy: tuple[str, ...]
    renumber: tuple[tuple[str, str], ...]  # (old_pg_id, new_pg_id)
    skip_dup: tuple[str, ...]


def content_key(frontmatter: dict) -> str:
    """Canonical content key for a decision, excluding its id."""
    without_id = {k: v for k, v in frontmatter.items() if k != "id"}
    return json.dumps(without_id, sort_keys=True, ensure_ascii=False)


def _num(decision_id: str) -> int | None:
    if not decision_id.startswith("dec-"):
        return None
    try:
        return int(decision_id[4:])
    except ValueError:
        return None


def plan_backfill(sqlite: list[DecRef], pg: list[DecRef]) -> BackfillPlan:
    sqlite_ids = {d.id for d in sqlite}
    sqlite_git = {d.git_hash for d in sqlite if d.git_hash}
    sqlite_content = {d.content_key for d in sqlite}

    nums = [n for d in (*sqlite, *pg) if (n := _num(d.id)) is not None]
    next_num = (max(nums) if nums else 0) + 1

    renumber: list[tuple[str, str]] = []
    skip_dup: list[str] = []
    for d in pg:
        is_dup = (d.git_hash and d.git_hash in sqlite_git) or (
            not d.git_hash and d.content_key in sqlite_content
        )
        if is_dup:
            skip_dup.append(d.id)
            continue
        if d.id in sqlite_ids:  # native row colliding with a legacy id we will copy
            renumber.append((d.id, f"dec-{next_num:03d}"))
            next_num += 1
        # native + non-colliding id -> leave untouched
    return BackfillPlan(
        copy_legacy=tuple(d.id for d in sqlite),
        renumber=tuple(renumber),
        skip_dup=tuple(skip_dup),
    )
