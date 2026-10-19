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
    r"^\[[^\]\n]+? (?:\(root-commit\) )?(?P<hash>[0-9a-f]{7,40})\] (?P<subject>[^\n]*)",
    re.MULTILINE,
)
_ARG = r"(?:\"[^\"]+\"|'[^']+'|\S+)"
# `git [-C dir] [-c k=v] [--flag] commit`: `commit` must be the subcommand.
_IS_COMMIT_CMD = re.compile(
    rf"\bgit(?P<opts>(?:\s+(?:-C|-c|--git-dir|--work-tree)\s+{_ARG}|\s+--?[\w-]+(?:=\S+)?)*)"
    r"\s+commit\b"
)
_DASH_C = re.compile(rf"-C\s+(?P<dir>{_ARG})")
_DRY_RUN = re.compile(r"\s--dry-run\b")
# Separates the independent calls of one Codex exec script; no `cd` crosses it.
_CALL_BREAK = "\n\0\n"
# `-m "subj"`, `-qam 'subj'`, `-m "$(cat <<'EOF'\nsubj`, `-F - <<'EOF'\nsubj`: first line
_MESSAGE = re.compile(
    r"\bcommit\b[^&|;]*?\s-[a-zA-Z]*m\s*(?:\"\$\(cat <<-?'?\"?(?:\w+)'?\"?\n(?P<heredoc>[^\n]+)"
    r"|\"(?P<dq>[^\"\n]+)|'(?P<sq>[^'\n]+)|(?P<bare>[^\s\"'&|;$]+))"
    r"|\bcommit\b[^&|;]*?\s-[a-zA-Z]*F\s*-\s*<<-?['\"]?\w+['\"]?\n(?P<stdin>[^\n]+)"
)
_QUIET = re.compile(r"\s(?:-[a-zA-Z]*q[a-zA-Z]*|--quiet)\b")
# Codex output carries `"exit_code": N`, sometimes inside an escaped JSON string.
_NONZERO_EXIT = re.compile(r'\\?"exit_code\\?"\s*:\s*[1-9]')
_FAILED = re.compile(
    r"nothing to commit|no changes added to commit|nothing added to commit"
    r"|exit code:? [1-9]|exited with code [1-9]",
    re.IGNORECASE,
)
# Codex's `exec` tool runs a JS script: tools.exec_command({cmd:"...", workdir:"..."})
_JS_STRING = re.compile(r'["\']?\b(?P<key>cmd|workdir)\b["\']?\s*:\s*(?P<lit>"(?:[^"\\]|\\.)*")')
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'')
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
        found: dict[str, list[str]] = {"cmd": []}
        for call in args.split("exec_command(")[1:]:
            fields: dict[str, str] = {}
            for m in _JS_STRING.finditer(call.split("})", 1)[0]):
                try:
                    fields.setdefault(m.group("key"), json.loads(m.group("lit")))
                except ValueError:
                    continue
            if "cmd" in fields:
                wd, cmd = fields.get("workdir"), fields["cmd"]
                found["cmd"].append(f"cd {shlex.quote(wd)} && {cmd}" if wd else cmd)
        return _CALL_BREAK.join(found["cmd"]), None
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


def _base_before(prefix: str, base: str | None) -> str | None:
    for segment in _SEGMENTS.split(prefix.rsplit(_CALL_BREAK, 1)[-1]):
        for m in _CD.finditer(segment):
            base = _join(base, m.group("dir"))
    return base


@dataclass(frozen=True)
class _CommitCmd:
    directory: str | None
    quiet: bool
    subject: str | None


def _commit_commands(command: str, workdir: str | None, workspace: str | None) -> list[_CommitCmd]:
    """Each `git commit` in the command: its directory (`-C` on top of the last `cd`)."""
    # `git commit` inside a quoted message is text, not a second commit.
    masked = _QUOTED.sub(lambda q: q[0][0] + "x" * (len(q[0]) - 2) + q[0][-1], command)
    matches = list(_IS_COMMIT_CMD.finditer(masked))
    found = []
    for i, m in enumerate(matches):
        base = _base_before(command[: m.start()], workdir or workspace)
        dash_c = _DASH_C.search(command[m.start("opts") : m.end("opts")])
        directory = _join(base, dash_c.group("dir")) if dash_c else base
        tail = command[m.start() : matches[i + 1].start() if i + 1 < len(matches) else None]
        message = _MESSAGE.search(tail)
        subject = next(g for g in message.groups() if g).strip() if message else None
        # Flags are read only before the message, so `-m 'run -q'` is not quiet.
        flags = tail[: message.start(next(k for k, v in message.groupdict().items() if v))
                     if message else None].split("\n", 1)[0]
        # A heredoc body may mention --dry-run, so only the command's own line counts.
        if _DRY_RUN.search(flags) or _DRY_RUN.search(_QUOTED.sub("", tail).split("\n", 1)[0]):
            continue
        found.append(_CommitCmd(directory, bool(_QUIET.search(flags)), subject))
    return found


def extract_commits(events: list[dict], workspace: str | None) -> list[CommitRef]:
    """Commits made by `git commit` tool calls, in event order.

    A printed `[branch hash]` line is paired with the non-quiet commits in order; a commit
    left without one is known by its subject, unless the result says the call failed.
    """
    calls: dict[str, tuple[str, str | None]] = {}
    found: list[CommitRef] = []
    for ev in events:
        content, call_id = ev["content"], ev.get("call_id")
        if ev["kind"] == "tool_call" and call_id:
            calls[call_id] = _call_command(content)
        elif ev["kind"] == "tool_result" and call_id in calls:
            commits = _commit_commands(*calls[call_id], workspace)
            if not commits:
                continue
            text = _result_text(content)
            printed = [(m.group("hash"), m.group("subject")) for m in _COMMIT_LINE.finditer(text)]
            loud = [c for c in commits if not c.quiet] or commits
            used: set[int] = set()
            for short, subject in printed:
                # Pair by subject first; a failed earlier commit must not shift the rest.
                free = [c for c in loud if id(c) not in used]
                same = [c for c in free if c.subject == subject]
                match = (same or free or loud[-1:])[0]
                used.add(id(match))
                found.append(CommitRef(short, ev["event_id"], match.directory))
            rejected = (
                content.get("is_error") is True
                or _FAILED.search(text)
                or _NONZERO_EXIT.search(json.dumps(content))
            )
            if rejected:
                continue
            for c in commits:
                if c.subject and id(c) not in used:
                    when = ev.get("occurred_at")
                    found.append(CommitRef(None, ev["event_id"], c.directory, c.subject, when))
    return found


def find_by_subject(subject: str, directory: str | None, before: datetime | None) -> str | None:
    """Hash of the commit with `subject` made in the window before `before`, if exactly one."""
    if not directory or before is None or not Path(directory).is_dir():
        return None
    # git compares whole seconds; a commit and its result often share one.
    since, until = before - _SUBJECT_WINDOW, before + timedelta(seconds=2)
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
