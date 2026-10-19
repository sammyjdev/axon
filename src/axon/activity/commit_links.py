"""Link the commits a session produced to that session (phase D, "why does this code exist").

Pure parsing of stored tool events, then idempotent writes through the repository.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

# `[main abc1234] subj`, `[main (root-commit) abc1234] subj`, `[detached HEAD abc1234] subj`
_COMMIT_LINE = re.compile(
    r"^\[[^\]\n]+? (?:\(root-commit\) )?(?P<hash>[0-9a-f]{7,40})\] ", re.MULTILINE
)
_IS_COMMIT_CMD = re.compile(r"\bgit\b[^&|;\n]*?\bcommit\b")
_DASH_C = re.compile(r"\bgit\s+(?:-\S+\s+)*?-C\s+(?P<dir>\"[^\"]+\"|'[^']+'|\S+)")
# `-m "subj"`, `-qam 'subj'`, `-m "$(cat <<'EOF'\nsubj`, `-F - <<'EOF'\nsubj`: first line
_MESSAGE = re.compile(
    r"\bcommit\b[^&|;]*?\s-[a-zA-Z]*m\s*(?:\"\$\(cat <<-?'?\"?(?:\w+)'?\"?\n(?P<heredoc>[^\n]+)"
    r"|\"(?P<dq>[^\"\n]+)|'(?P<sq>[^'\n]+))"
    r"|\bcommit\b[^&|;]*?\s-[a-zA-Z]*F\s*-\s*<<-?['\"]?\w+['\"]?\n(?P<stdin>[^\n]+)"
)
_FAILED = re.compile(r"nothing to commit|no changes added to commit|nothing added to commit")
# Codex's `exec` tool runs a JS script: tools.exec_command({cmd:"...", workdir:"..."})
_JS_STRING = re.compile(r'\b(?P<key>cmd|workdir)\s*:\s*(?P<lit>"(?:[^"\\]|\\.)*")')
# A quiet commit is looked up in the log this long before its result was recorded.
_SUBJECT_WINDOW = timedelta(minutes=15)
_SEGMENTS = re.compile(r"&&|\|\||;|\n")
_CD = re.compile(r"^\s*cd\s+(?P<dir>\"[^\"]+\"|'[^']+'|[^\s;&|]+)")


@dataclass(frozen=True)
class CommitRef:
    """A printed commit (`short_hash`) or a quiet one known only by `subject`."""

    short_hash: str | None
    event_id: str
    directory: str | None
    subject: str | None = None
    occurred_at: datetime | None = None


def _unquote(value: str) -> str:
    return value[1:-1] if value[:1] in "\"'" and value[-1:] == value[:1] else value


def _call_command(content: dict) -> tuple[str, str | None]:
    """Return (command text, workdir) for a Claude `tool_use` or Codex `function_call`."""
    args = content.get("input")
    if isinstance(args, str):
        found: dict[str, list[str]] = {"cmd": [], "workdir": []}
        for m in _JS_STRING.finditer(args):
            try:
                found[m.group("key")].append(json.loads(m.group("lit")))
            except ValueError:
                continue
        return "\n".join(found["cmd"]), next(iter(found["workdir"]), None)
    if args is None:
        raw = content.get("arguments")
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            args = None
    if not isinstance(args, dict):
        return "", None
    cmd = args.get("command") or args.get("cmd") or ""
    if isinstance(cmd, list):
        is_shell = cmd and str(cmd[0]).endswith(("sh", "bash", "zsh"))
        cmd = cmd[-1] if is_shell else shlex.join(map(str, cmd))
    workdir = args.get("workdir") or args.get("cwd")
    return str(cmd), workdir if isinstance(workdir, str) else None


def _result_text(content: dict) -> str:
    out = content.get("content", content.get("output", ""))
    if isinstance(out, list):
        out = "\n".join(_inner_output(b.get("text", "")) for b in out if isinstance(b, dict))
    if not isinstance(out, str):
        return ""
    return _inner_output(out)


def _inner_output(out: str) -> str:
    """Codex wraps command output as `{"output": "...", ...}`; unwrap it."""
    if out.lstrip().startswith("{"):
        try:
            inner = json.loads(out).get("output")
            if isinstance(inner, str):
                return inner
        except (ValueError, AttributeError):
            pass
    return out


def _join(base: str | None, d: str) -> str:
    d = _unquote(d)
    return d if d.startswith("/") or not base else str(Path(base) / d)


def _directory(command: str, workdir: str | None, workspace: str | None) -> str | None:
    """Where the commit ran: its own `-C`, on top of the last `cd` before it."""
    base = workdir or workspace
    for segment in _SEGMENTS.split(command):
        if _IS_COMMIT_CMD.search(segment):
            m = _DASH_C.search(segment)
            return _join(base, m.group("dir")) if m else base
        for m in _CD.finditer(segment):
            base = _join(base, m.group("dir"))
    return base


def extract_commits(events: list[dict], workspace: str | None) -> list[CommitRef]:
    """Commits printed by `git commit` tool results, in event order."""
    calls: dict[str, tuple[str, str | None]] = {}
    found: list[CommitRef] = []
    for ev in events:
        content, call_id = ev["content"], ev.get("call_id")
        if ev["kind"] == "tool_call" and call_id:
            calls[call_id] = _call_command(content)
        elif ev["kind"] == "tool_result" and call_id in calls:
            command, workdir = calls[call_id]
            if not _IS_COMMIT_CMD.search(command):
                continue
            directory = _directory(command, workdir, workspace)
            text = _result_text(content)
            printed = list(_COMMIT_LINE.finditer(text))
            for m in printed:
                found.append(CommitRef(m.group("hash"), ev["event_id"], directory))
            message = _MESSAGE.search(command)
            rejected = content.get("is_error") is True or _FAILED.search(text)
            if not printed and message and not rejected:
                subject = next(g for g in message.groups() if g)
                when = ev.get("occurred_at")
                found.append(CommitRef(None, ev["event_id"], directory, subject.strip(), when))
    return found


def find_by_subject(subject: str, directory: str | None, before: datetime | None) -> str | None:
    """Hash of the commit with `subject` made in the window before `before`, if exactly one."""
    if not directory or before is None or not Path(directory).is_dir():
        return None
    since, until = before - _SUBJECT_WINDOW, before + timedelta(minutes=1)
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "-C", directory, "log", "--all", "--format=%H%x00%s",  # noqa: S607
             f"--since={since.isoformat()}", f"--until={until.isoformat()}"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    hashes = {h for h, _, s in (ln.partition("\0") for ln in out.splitlines()) if s == subject}
    return hashes.pop() if len(hashes) == 1 else None


def resolve_full_hash(short: str, directory: str | None) -> str:
    """Full hash via `git rev-parse` when the directory exists, else the short hash."""
    if not directory or not Path(directory).is_dir():
        return short
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "-C", directory, "rev-parse", "--verify", "--quiet", f"{short}^{{commit}}"],  # noqa: S607
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return short
    return out if re.fullmatch(r"[0-9a-f]{40}", out) else short


async def link_commits(repo, session_id: str) -> int:
    """Link every commit the session's tool results printed; returns links newly added."""
    session = await repo.get_session(session_id)
    if session is None:
        return 0
    events = await repo.list_tool_events(session_id)
    added = 0
    for ref in extract_commits(events, session.get("workspace")):
        if ref.short_hash is None:
            commit = find_by_subject(ref.subject or "", ref.directory, ref.occurred_at)
            if commit is None:
                continue
        else:
            commit = resolve_full_hash(ref.short_hash, ref.directory)
        if await repo.add_commit_link(commit=commit, session_id=session_id, event_id=ref.event_id):
            added += 1
    return added


async def link_all_commits(repo) -> tuple[int, int]:
    """Backfill every stored session; returns (sessions scanned, links added)."""
    ids = await repo.list_session_ids()
    return len(ids), sum([await link_commits(repo, sid) for sid in ids])
