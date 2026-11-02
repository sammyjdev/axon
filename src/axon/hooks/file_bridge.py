"""File-based context bridge — maintains ``<repo>/.axon/context.md``.

Agents without MCP (e.g. Cursor) read this file directly. It is refreshed on
git events and on session end. Writes are atomic (temp file + rename).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path

from axon.activity.models import RecentSession
from axon.activity.repository import PostgresActivityRepository
from axon.context.brief_budget import BRIEF_BUDGET, pack_items
from axon.context.fence import escape_untrusted, fence
from axon.core.decision import Decision
from axon.core.repo_identity import repo_identity
from axon.observability.friction import FrictionPattern
from axon.store.lessons import LessonHandle, LessonStore, resolve_lessons_dsn
from axon.store.pg_decision_repository import InjectableDecisions
from axon.store.session_store import SessionStore

logger = logging.getLogger(__name__)

# One line, the session that just closed. Prompts, touched files and commit subjects
# are derivable from the transcript and from git, so they are not copied in (CTX-05).
_SESSION_LIMIT = 1
_FILE_LIMIT = 8
_COMMIT_LIMIT = 3
_PROMPT_CHARS = 160
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


def _decoded(args: object) -> object:
    if isinstance(args, str):
        try:
            return json.loads(args)
        except ValueError:
            return args
    return args


def _call_workdir(call: dict) -> str | None:
    """The directory a Codex call ran in: its arguments' (or input's) workdir."""
    for src in (_decoded(call.get("arguments")), call.get("input")):
        if isinstance(src, dict) and isinstance(src.get("workdir"), str):
            return src["workdir"]
    return None


def _removed_worktree(ws: Path, root: Path) -> Path | None:
    """The worktree a missing workspace was, once removed after its merge left no .git.

    Outside the repo it is the workspace itself; inside, only Claude Code's
    .claude/worktrees/<name> is one (any other missing path is a subdirectory of the repo).
    """
    if not ws.is_relative_to(root):
        return ws
    parts = ws.relative_to(root).parts
    if parts[:2] == (".claude", "worktrees") and len(parts) > 2:
        return root.joinpath(*parts[:3])
    return None


def _touched_files(session: RecentSession, root: Path) -> list[str]:
    """Paths relative to the repo root (or the session's worktree), first touch first."""
    bases = [root]
    if session.workspace:
        ws = Path(session.workspace)
        top = _git_top(ws) if ws.exists() else _removed_worktree(ws, root)
        if top and top != root:
            bases.insert(0, top)  # the worktree first: it may sit inside the repo
    seen: dict[str, None] = {}
    for call in session.edit_calls:
        inp = call.get("input")
        if call.get("name") in _EDIT_TOOLS and isinstance(inp, dict):
            raw = [inp.get("file_path") or inp.get("notebook_path")]
        else:
            # Codex arguments are a JSON string: decode it, or "ação" would read "a\u00e7\u00e3o".
            args = _decoded(call.get("arguments"))
            raw = [m for s in _strings({**call, "arguments": args}) for m in _PATCH_FILE.findall(s)]
        # A patch run from another directory names paths relative to that directory.
        here = Path(session.workspace or "") / (_call_workdir(call) or "")
        for r in raw:
            if not isinstance(r, str) or not r.strip():
                continue
            path = Path(r.strip())
            if not path.is_absolute():
                if not here.is_absolute():
                    continue
                path = here / path
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


def _session_pointer(session: RecentSession) -> str:
    """One stable line for the newest session. No prompt, path, or commit text."""
    last = session.last_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    else:
        last = last.astimezone(UTC)
    return (
        f"Last session: {last:%Y-%m-%d %H:%M} UTC · {session.harness} · "
        f"`{session.session_id[:8]}`"
    )


def _render_sessions(sessions: Sequence[RecentSession], _root: Path) -> list[str]:
    """The last-session pointer only. File lists are not rendered (CTX-05)."""
    if not sessions:
        return []
    return ["", _session_pointer(sessions[0]), ""]


def _decision_line(decision: Decision) -> str:
    summary = " ".join(decision.summary.split())
    return f"- `{decision.id}` ({decision.status}) — {summary}"


def _lesson_line(handle: LessonHandle) -> str:
    return f"- {handle.kind}: {', '.join(handle.triggers)}"


def _header(repo: str, active: list[Decision], draft_count: int) -> str:
    lines = [f"# AXON context — {repo}"]
    if active:
        # The id is stable store state. A wall clock would break prompt caching (CTX-07).
        lines.append(f"Newest decision: {active[0].id}")
    else:
        lines.append("No active decisions.")
    if draft_count > 0:
        lines.append(f"{draft_count} drafts - axon adr list")
    return "\n".join(lines) + "\n"


def _brief_items(
    active: Sequence[Decision],
    lessons: Sequence[LessonHandle],
    sessions: Sequence[RecentSession],
) -> list[str]:
    items = [_decision_line(decision) for decision in active]
    if lessons:
        # Handles only. The call is named because a list with no way to read one is inert.
        items.append("Triggers only. Call axon_search_lessons.")
        items.extend(_lesson_line(handle) for handle in lessons)
    if sessions:
        items.append(_session_pointer(sessions[0]))
    return items


def _render(
    repo: str,
    decisions: list[Decision],
    friction: Sequence[FrictionPattern] = (),
    lessons: Sequence[LessonHandle] = (),
    sessions: Sequence[RecentSession] = (),
    root: Path | None = None,
    *,
    draft_count: int = 0,
) -> str:
    """Render the fenced brief. Only ``active`` decisions are admitted (CTX-01).

    ``friction`` and ``root`` are accepted so existing callers keep working.
    Friction is not a brief item (AD-3). ``root`` used to resolve touched-file
    lists; those lists are not rendered (CTX-05).
    """
    del friction, root
    active = [decision for decision in decisions if decision.status == "active"]
    fixed = escape_untrusted(_header(repo, active, draft_count))
    items = [
        escape_untrusted(item) for item in _brief_items(active, lessons, sessions)
    ]
    room = BRIEF_BUDGET - len(fence(fixed).encode("utf-8"))
    packed = pack_items(items, room) if room > 0 else ""
    return fence(fixed + packed)


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


async def _load_injectable(store: object, repo: str) -> InjectableDecisions:
    """Admission query. A test double that only lists decisions is filtered here."""
    finder = getattr(store, "find_injectable", None)
    if finder is None and hasattr(store, "_decisions"):
        finder = getattr(await store._decisions(), "find_injectable", None)
    if finder is not None:
        found = await finder(repo)
        return found
    rows = await store.find_decisions_by_repo(repo, limit=10_000)  # type: ignore[attr-defined]
    return InjectableDecisions(
        active=[row for row in rows if row.status == "active"],
        draft_count=sum(1 for row in rows if row.status == "draft"),
    )


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
    injectable = await _load_injectable(store, repo)
    owns_activity = activity is None
    if activity is None:
        from axon.config.runtime import load_runtime_config

        activity = PostgresActivityRepository(load_runtime_config().pg_url)
    try:
        sessions = await activity.recent_sessions(repo, limit=_SESSION_LIMIT)
    finally:
        if owns_activity:
            await activity.close()
    content = _render(
        repo,
        injectable.active,
        friction,
        await _lesson_catalog(),
        sessions,
        root,
        draft_count=injectable.draft_count,
    )

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
