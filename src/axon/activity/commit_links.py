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
# Separates the independent calls of one Codex exec script; no `cd` crosses it.
_CALL_BREAK = "__AXON_CALL_BREAK__"
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)(\w+)\1(?!<)")
_HEREDOC_TOKEN = "__AXON_HEREDOC_{}__"  # noqa: S105
_HEREDOC_REF = re.compile(r"__AXON_HEREDOC_(\d+)__")
_OPERATORS = set(";&|\n")
# git global options that take a value as the next word
_GIT_VALUE_OPTS = {"-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
# Codex output carries `"exit_code": N`, sometimes inside an escaped JSON string.
_NONZERO_EXIT = re.compile(r'\\?"exit_code\\?"\s*:\s*[1-9]')
_FAILED = re.compile(
    r"nothing to commit|no changes added to commit|nothing added to commit"
    r"|exit code:? [1-9]|exited with code [1-9]",
    re.IGNORECASE,
)
# Codex's `exec` tool runs a JS script: tools.exec_command({cmd:"...", workdir:"..."})
_JS_STRING = re.compile(
    r"""["']?\b(?P<key>cmd|workdir)\b["']?\s*:\s*(?P<lit>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')"""
)
# A quiet commit is looked up in the log this long before its result was recorded.
_SUBJECT_WINDOW = timedelta(minutes=15)


@dataclass(frozen=True)
class CommitRef:
    """A printed commit (`short_hash`) or a quiet one known only by `subject`."""

    short_hash: str | None
    event_id: str
    directory: str | None
    subject: str | None = None
    occurred_at: datetime | None = None


def _call_command(content: dict) -> tuple[str, str | None]:
    """Return (command text, workdir) for a Claude `tool_use` or Codex `function_call`."""
    args = content.get("input")
    if isinstance(args, str):
        found: dict[str, list[str]] = {"cmd": []}
        for call in args.split("exec_command(")[1:]:
            fields: dict[str, str] = {}
            for m in _JS_STRING.finditer(call.split("})", 1)[0]):
                lit = m.group("lit")
                if lit[0] == "'":
                    lit = json.dumps(lit[1:-1].replace("\\'", "'"))
                try:
                    fields.setdefault(m.group("key"), json.loads(lit))
                except ValueError:
                    continue
            if "cmd" in fields:
                wd, cmd = fields.get("workdir"), fields["cmd"]
                found["cmd"].append(f"cd {shlex.quote(wd)} && {cmd}" if wd else cmd)
        return f"\n{_CALL_BREAK}\n".join(found["cmd"]), None
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
    d = str(Path(d).expanduser())
    return d if d.startswith("/") or not base else str(Path(base) / d)


def _split_heredocs(command: str) -> tuple[str, list[str]]:
    """Cut heredoc bodies out of the command, leaving a token where each `<<EOF` was."""
    lines, code, bodies = command.split("\n"), [], []
    i = 0
    while i < len(lines):
        pending: list[tuple[int, str]] = []

        def _token(m: re.Match) -> str:
            bodies.append("")
            pending.append((len(bodies) - 1, m.group(2)))
            return " " + _HEREDOC_TOKEN.format(len(bodies) - 1) + " "

        code.append(_HEREDOC.sub(_token, lines[i]))
        i += 1
        for index, delimiter in pending:
            body = []
            while i < len(lines) and lines[i].strip() != delimiter:
                body.append(lines[i])
                i += 1
            i += 1
            bodies[index] = "\n".join(body)
    return "\n".join(code), bodies


def _simple_commands(code: str) -> list[list[str]]:
    """Words of each simple command, split on ; & | and unquoted newlines."""
    lexer = shlex.shlex(code, posix=True, punctuation_chars=";&|\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    commands: list[list[str]] = [[]]
    try:
        for word in lexer:
            if word and set(word) <= _OPERATORS:
                commands.append([])
            else:
                commands[-1].append(word)
    except ValueError:  # unbalanced quotes: nothing can be trusted
        return []
    return [c for c in commands if c]


@dataclass(frozen=True)
class _CommitCmd:
    directory: str | None
    quiet: bool
    subject: str | None


def _first_line(text: str) -> str | None:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), None)


def _parse_commit(args: list[str], bodies: list[str]) -> tuple[bool, bool, str | None]:
    """(quiet, dry_run, subject) from the words after `git commit`."""
    def expand(value: str) -> str:
        # `-m "$(cat <<'EOF' ... EOF)"`: the message is the heredoc body.
        ref = _HEREDOC_REF.search(value)
        return bodies[int(ref.group(1))] if ref else value

    quiet = dry_run = False
    subject = None
    stdin = next((expand(a) for a in args if _HEREDOC_REF.fullmatch(a)), "")
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            break
        if a in ("--quiet",):
            quiet = True
        elif a == "--dry-run":
            dry_run = True
        elif a.startswith(("--message=", "--file=")) or a in ("--message", "--file"):
            value = a.split("=", 1)[1] if "=" in a else (args[i] if i < len(args) else "")
            i += "=" not in a
            if a.startswith("--message"):
                subject = _first_line(expand(value))
            elif value == "-":
                subject = _first_line(stdin)
        elif a.startswith("-") and not a.startswith("--") and len(a) > 1:
            for k, flag in enumerate(a[1:], start=1):
                if flag == "q":
                    quiet = True
                elif flag in "mFCct":
                    value = a[k + 1 :] or (args[i] if i < len(args) else "")
                    i += not a[k + 1 :]
                    if flag == "m":
                        subject = _first_line(expand(value))
                    elif flag == "F" and value == "-":
                        subject = _first_line(stdin)
                    break
    return quiet, dry_run, subject


def _commit_commands(command: str, workdir: str | None, workspace: str | None) -> list[_CommitCmd]:
    """Each `git commit` the command runs, with the directory it runs in."""
    start = workdir or workspace
    code, bodies = _split_heredocs(command)
    base, found = start, []
    for words in _simple_commands(code):
        while words and (re.fullmatch(r"\w+=\S*", words[0]) or words[0] in ("env", "command")):
            words = words[1:]
        if words[:1] == ["timeout"] and len(words) > 2:
            words = words[2:]
        if not words:
            continue
        if words[0] == _CALL_BREAK:
            base = start
        elif words[0] == "cd" and len(words) > 1:
            base = _join(base, words[1])
        elif words[0] == "git":
            directory, i = base, 1
            while i < len(words) and words[i].startswith("-"):
                if words[i] == "-C" and i + 1 < len(words):
                    directory = _join(directory, words[i + 1])
                i += 2 if words[i] in _GIT_VALUE_OPTS or words[i] == "-C" else 1
            if words[i : i + 1] == ["commit"]:
                quiet, dry_run, subject = _parse_commit(words[i + 1 :], bodies)
                if not dry_run:
                    found.append(_CommitCmd(directory, quiet, subject))
    return found


def extract_commits(events: list[dict], workspace: str | None) -> list[CommitRef]:
    """Commits made by `git commit` tool calls, in event order.

    A printed `[branch hash]` line is paired with the non-quiet commits in order; a commit
    left without one is known by its subject, unless the result says the call failed.
    """
    # Calls first: a result can be stored ahead of its call when both share a timestamp.
    calls: dict[str, tuple[str, str | None]] = {
        ev["call_id"]: _call_command(ev["content"])
        for ev in events
        if ev["kind"] == "tool_call" and ev.get("call_id")
    }
    found: list[CommitRef] = []
    for ev in events:
        content, call_id = ev["content"], ev.get("call_id")
        if ev["kind"] == "tool_result" and call_id in calls:
            commits = _commit_commands(*calls[call_id], workspace)
            if not commits:
                continue
            text = _result_text(content)
            printed = [(m.group("hash"), m.group("subject")) for m in _COMMIT_LINE.finditer(text)]
            loud = [c for c in commits if not c.quiet] or commits
            used: set[int] = set()
            refs: list[tuple[int, CommitRef]] = []
            for short, subject in printed:
                # Pair by subject first; a failed earlier commit must not shift the rest.
                free = [c for c in loud if id(c) not in used]
                same = [c for c in free if c.subject == subject]
                match = (same or free or loud[-1:])[0]
                used.add(id(match))
                position = next(n for n, c in enumerate(commits) if c is match)
                refs.append((position, CommitRef(short, ev["event_id"], match.directory)))
            rejected = (
                content.get("is_error") is True
                or _FAILED.search(text)
                or _NONZERO_EXIT.search(json.dumps(content))
            )
            for n, c in enumerate(commits):
                if c.subject and id(c) not in used and not rejected:
                    when = ev.get("occurred_at")
                    refs.append((n, CommitRef(None, ev["event_id"], c.directory, c.subject, when)))
            found.extend(ref for _, ref in sorted(refs, key=lambda r: r[0]))
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
