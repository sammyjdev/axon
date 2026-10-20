"""`axon why`: the sessions, prompts and decisions behind a commit or a file's recent commits."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROMPT_CHARS = 200


class WhyError(Exception):
    pass


def _git(directory: Path, *args: str) -> str:
    try:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(directory), *args],  # noqa: S607
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise WhyError(detail.strip() or "git failed") from exc


def _oneline(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _PROMPT_CHARS else flat[: _PROMPT_CHARS - 3] + "..."


async def _explain_commit(repo, store, directory: Path, ref: str) -> str:
    full, date, subject = _git(directory, "show", "-s", "--format=%H%n%as%n%s", ref).split("\n", 2)
    lines = [f"commit {full[:10]}  {date}  {subject}"]
    origins = await repo.find_commit_origins(full)
    if not origins:
        lines.append("  no session is linked to this commit")
    for o in origins:
        when = o["occurred_at"].strftime("%Y-%m-%d %H:%M")
        project = o["project"] or "-"
        lines.append(f"  session {o['session_id']}  {o['harness']}  project {project}  {when}")
        prompts = await repo.last_user_prompts(o["session_id"], before=o["occurred_at"], limit=3)
        for p in prompts:
            lines.append(f"    prompt: {_oneline(p)}")
        if not prompts:
            lines.append("    no typed prompt stored before the commit")
    decisions = await store.find_decisions_by_git_hash(full) if store is not None else []
    for decision in decisions:
        lines.append(f"  decision {decision.id}: {_oneline(decision.summary)}")
    return "\n".join(lines)


async def explain(repo, store, target: str, *, cwd: Path) -> str:
    """Plain-text account of `target`, a file path or a commit-ish."""
    path = Path(target).expanduser()
    path = path if path.is_absolute() else cwd / path
    if path.is_file():
        directory = path.parent
        log = _git(directory, "log", "-n", "5", "--follow", "--format=%H", "--", path.name)
        hashes = log.split()
        if not hashes:
            return f"{target}: no commit touches this file yet"
        return "\n\n".join([await _explain_commit(repo, store, directory, h) for h in hashes])
    return await _explain_commit(repo, store, cwd, target)
