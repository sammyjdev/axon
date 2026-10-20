"""File-based context bridge — maintains ``<repo>/.axon/context.md``.

Agents without MCP (e.g. Cursor) read this file directly. It is refreshed on
git events and on session end. Writes are atomic (temp file + rename).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from axon.activity.models import RecentSession
from axon.activity.repository import PostgresActivityRepository
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
# Three sessions: the one just closed plus the two before it covers "where was I" and a
# parallel lane, at ~6 lines each, and keeps the file well inside _MAX_LINES with 15
# decisions and the lesson catalogue beside it.
_SESSION_LIMIT = 3
_FILE_LIMIT = 8
_COMMIT_LIMIT = 3
_PROMPT_CHARS = 160
# Every line here is prepended to every session in the repo.
_MAX_LINES = 100
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Codex patches arrive as text, sometimes inside JS source, so `\n` may be escaped.
_PATCH_FILE = re.compile(r"\*\*\* (?:(?:Update|Add|Delete) File|Move to): (.+?)(?=\\n|\n|\"|$)")


def _one_line(text: str | None) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= _PROMPT_CHARS else flat[: _PROMPT_CHARS - 1] + "…"


def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _git_top(path: Path) -> Path | None:
    for p in (path, *path.parents):
        if (p / ".git").exists():
            return p
    return None


def _touched_files(session: RecentSession, root: Path) -> list[str]:
    """Paths relative to the repo root (or the session's worktree), first touch first."""
    bases = [root]
    if session.workspace and (top := _git_top(Path(session.workspace))) and top != root:
        bases.append(top)
    seen: dict[str, None] = {}
    for call in session.edit_calls:
        inp = call.get("input")
        if call.get("name") in _EDIT_TOOLS and isinstance(inp, dict):
            raw = [inp.get("file_path") or inp.get("notebook_path")]
        else:
            raw = [m for s in _strings(call) for m in _PATCH_FILE.findall(s)]
        for r in raw:
            if not isinstance(r, str) or not r.strip():
                continue
            path = Path(r.strip())
            if not path.is_absolute():
                if not session.workspace:
                    continue
                path = Path(session.workspace) / path
            path = Path(os.path.normpath(path))
            for base in bases:
                if path.is_relative_to(base):
                    seen.setdefault(path.relative_to(base).as_posix())
                    break
    return list(seen)


def _commit_line(root: Path, sha: str) -> str:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "show", "-s", "--format=%h %s", sha, "--"],  # noqa: S607
            cwd=root, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        if out:
            return _one_line(out)
    except (OSError, subprocess.SubprocessError):
        pass
    return sha[:7]


def _render_sessions(sessions: Sequence[RecentSession], root: Path) -> list[str]:
    lines = ["", "## Recent sessions", ""]
    for s in sessions:
        lines.append(f"### {s.last_at:%Y-%m-%d %H:%M} UTC · {s.harness} · `{s.session_id[:8]}`")
        if s.first_prompt:
            lines.append(f"- first ask: {_one_line(s.first_prompt)}")
        if s.last_prompt and s.last_prompt != s.first_prompt:
            lines.append(f"- last ask: {_one_line(s.last_prompt)}")
        files = _touched_files(s, root)
        if files:
            shown = ", ".join(f"`{f}`" for f in files[:_FILE_LIMIT])
            more = f" +{len(files) - _FILE_LIMIT} more" if len(files) > _FILE_LIMIT else ""
            lines.append(f"- files: {shown}{more}")
        commits = list(dict.fromkeys(s.commits))
        for sha in commits[:_COMMIT_LIMIT]:
            lines.append(f"- commit {_commit_line(root, sha)}")
        if len(commits) > _COMMIT_LIMIT:
            lines.append(f"- _+{len(commits) - _COMMIT_LIMIT} more commit(s)_")
        lines.append("")
    return lines[:-1]


def _render(
    repo: str,
    decisions: list[Decision],
    friction: Sequence[FrictionPattern] = (),
    lessons: Sequence[LessonHandle] = (),
    sessions: Sequence[RecentSession] = (),
    root: Path | None = None,
) -> str:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    lines = [
        f"# AXON context — {repo}",
        "",
        f"_Updated {now} · {len(decisions)} recent decision(s)._",
        "",
    ]
    if sessions:
        lines += _render_sessions(sessions, root or Path.cwd())[1:] + [""]
    lines += [
        "## Recent decisions",
        "",
    ]
    if decisions:
        lines += [f"- `{d.id}` ({d.status}) — {' '.join(d.summary.split())}" for d in decisions]
    else:
        lines.append("_None captured yet._")
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
            "Triggers only. Call `axon_search_lessons` with the situation you are in to read one.",
            "",
        ]
        lines += [f"- {h.kind}: {', '.join(h.triggers)}" for h in shown]
        if more:
            lines.append(f"- _...{more} more, all reachable through `axon_search_lessons`._")
    lines.append("")
    # Backstop only: the limits above keep a full file near 80 lines.
    return "\n".join("\n".join(lines).splitlines()[:_MAX_LINES]) + "\n"


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
    activity: PostgresActivityRepository | None = None,
) -> Path:
    """Write ``<repo_root>/.axon/context.md`` from the repo's recent sessions and decisions.

    Everything is read before anything is written, so a database that is down raises here
    and the previous file stays in place. The write is atomic. Returns the path written.
    """
    root = Path(repo_root)
    repo = repo_identity(root)
    decisions = await store.find_decisions_by_repo(repo, limit=_RECENT_LIMIT)
    owns_activity = activity is None
    if activity is None:
        from axon.config.runtime import load_runtime_config

        activity = PostgresActivityRepository(load_runtime_config().pg_url)
    try:
        sessions = await activity.recent_sessions(repo, limit=_SESSION_LIMIT)
    finally:
        if owns_activity:
            await activity.close()
    content = _render(repo, decisions, friction, await _lesson_catalog(), sessions, root)

    axon_dir = root / ".axon"
    axon_dir.mkdir(parents=True, exist_ok=True)
    target = axon_dir / "context.md"
    tmp = axon_dir / "context.md.tmp"
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)
    return target


async def refresh_context_for_cwd(cwd: Path | str) -> Path | None:
    """Regenerate the context file of the repo holding `cwd`; quiet no-op outside a repo.

    Never raises: it runs at session end, where a failure must not reach the agent.
    """
    try:
        top = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
            cwd=cwd, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    store = SessionStore()
    try:
        return await update_context_file(Path(top), store=store)
    except Exception as exc:  # noqa: BLE001 - the previous file stays, the session ends
        logger.warning("context.md refresh skipped: %s", exc)
        return None
    finally:
        await store.close()
