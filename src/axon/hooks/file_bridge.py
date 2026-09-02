"""File-based context bridge — maintains ``<repo>/.axon/context.md``.

Agents without MCP (e.g. Cursor) read this file directly. It is refreshed on
git events and on session end. Writes are atomic (temp file + rename).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from axon.core.decision import Decision
from axon.core.repo_identity import repo_identity
from axon.observability.friction import FrictionPattern
from axon.store.lessons import LessonHandle, LessonStore, resolve_lessons_dsn
from axon.store.session_store import SessionStore

logger = logging.getLogger(__name__)

_RECENT_LIMIT = 15
# The catalogue is prepended to every session, and the corpus only grows - lessons are added
# and never retired - so an uncapped list is a slow leak into every session's token budget.
# Newest first, because the lesson written today is the one most likely to be about a
# mistake still being made.
_LESSON_LIMIT = 24


def _render(
    repo: str,
    decisions: list[Decision],
    friction: Sequence[FrictionPattern] = (),
    lessons: Sequence[LessonHandle] = (),
) -> str:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    lines = [
        f"# AXON context — {repo}",
        "",
        f"_Updated {now} · {len(decisions)} recent decision(s)._",
        "",
        "## Recent decisions",
        "",
    ]
    if decisions:
        lines += [f"- `{d.id}` ({d.status}) — {d.summary}" for d in decisions]
    else:
        lines.append("_None captured yet._")

    symbols = sorted({symbol for d in decisions for symbol in d.symbols})
    lines += ["", "## Active symbols", ""]
    lines += [f"- `{symbol}`" for symbol in symbols] if symbols else ["_None._"]
    if friction:
        lines += ["", "## Recurring friction", ""]
        lines += [
            f"- `{pattern.reason_code}` via {pattern.caller} (ctx={pattern.ctx}) - "
            f"{pattern.count}x across {pattern.distinct_days} days"
            for pattern in friction[:5]
        ]
    if lessons:
        # Handles, not content: the mistake/tell/fix stay behind the call. And the call is
        # named on the section, because a list nobody can act on is a phone book with no
        # telephone - the measured problem is not that lessons are hard to fetch, it is
        # that nothing says they exist (4.1% of sessions asked, in the 30 days to
        # 2026-09-01). No empty heading when there are none: it would spend the tokens and
        # teach the reader that the section is usually empty.
        shown = list(lessons[:_LESSON_LIMIT])
        more = len(lessons) - len(shown)
        lines += [
            "",
            f"## Lessons ({len(lessons)} recorded)",
            "",
            "Triggers only. Call `search_lessons` with the situation you are in to read one.",
            "",
        ]
        lines += [f"- {h.kind}: {', '.join(h.triggers)}" for h in shown]
        if more:
            lines.append(f"- _...{more} more, all reachable through `search_lessons`._")
    lines.append("")
    return "\n".join(lines)


async def _lesson_catalog() -> list[LessonHandle]:
    """The catalogue, or nothing at all - never an exception.

    `resolve_lessons_dsn()` raises RuntimeError when `AXON_LESSONS_PG_URL` is unset, with no
    fallback and for a good reason: silently reusing the shared DSN would put one client's
    lessons in the database every other store writes to. That refusal is right where it
    lives and fatal here. This file is what a SessionStart hook cats, so letting the
    exception out trades "no lesson catalogue" for "no context file", on every machine that
    never set the variable.

    The lessons database is also a second server, reachable independently of the session
    store, so `except Exception` and not just the RuntimeError: a context file that fails to
    write because a database the OTHER sections do not need was down is the same bad trade.
    """
    try:
        store = LessonStore(dsn=resolve_lessons_dsn())
        return await store.catalog()
    except Exception as exc:  # noqa: BLE001 - the catalogue is the optional section
        logger.debug("lesson catalogue omitted: %s", exc)
        return []


async def update_context_file(
    repo_root: Path | str,
    *,
    store: SessionStore,
    friction: Sequence[FrictionPattern] = (),
) -> Path:
    """Write ``<repo_root>/.axon/context.md`` from the repo's recent decisions.

    The write is atomic. Returns the path written.
    """
    root = Path(repo_root)
    repo = repo_identity(root)
    decisions = await store.find_decisions_by_repo(repo, limit=_RECENT_LIMIT)
    content = _render(repo, decisions, friction, await _lesson_catalog())

    axon_dir = root / ".axon"
    axon_dir.mkdir(parents=True, exist_ok=True)
    target = axon_dir / "context.md"
    tmp = axon_dir / "context.md.tmp"
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)
    return target
