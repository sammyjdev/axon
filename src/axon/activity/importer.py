"""Import agent transcripts (Claude Code, Codex) into the activity history.

One entry point for the SessionEnd hook and for `axon activity backfill`: the
harness is told by the file's content, the cursor makes a re-import cheap, and
a source's sessions, events and cursor land in one transaction. When the
database is down nothing is lost: the cursor does not move, and the next
import reads from the same offset - the transcript itself is the spool.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from axon.activity.adapters.claude_code import parse_claude_code_session
from axon.activity.adapters.codex import parse_codex_session
from axon.activity.repository import PostgresActivityRepository

Harness = Literal["claude-code", "codex"]

# Off-limits tree (employer work): a session there is skipped on its cwd alone.
RESTRICTED_ROOT = Path(
    os.environ.get("AXON_RESTRICTED_ROOT") or Path.home() / "dev" / "client-a"
).expanduser()
_SNIFF_LINES = 200


@dataclass
class ImportResult:
    path: Path
    harness: Harness | None
    sessions: int = 0
    events: int = 0
    redactions: int = 0
    skipped: str | None = None


def _sniff(path: Path) -> tuple[Harness | None, str | None]:
    """(harness, cwd) from the first lines, reading no further than the cwd."""
    harness: Harness | None = None
    with open(path, "rb") as handle:
        for _ in range(_SNIFF_LINES):
            line = handle.readline()
            if not line:
                break
            try:
                entry = json.loads(line)
            except ValueError:  # bad JSON or invalid UTF-8
                continue
            if not isinstance(entry, dict):
                continue
            payload = entry.get("payload")
            if entry.get("type") == "session_meta" and isinstance(payload, dict):
                cwd = payload.get("cwd")
                return "codex", cwd if isinstance(cwd, str) else None
            if "sessionId" in entry:
                harness = "claude-code"
                if isinstance(entry.get("cwd"), str):
                    return harness, entry["cwd"]
    return harness, None


def _enters_restricted(path: Path) -> bool:
    """True when any line's cwd (Claude `cwd`, Codex `payload.cwd`) is under the
    restricted tree."""
    # Only lines that can carry a cwd key are parsed: one spelling it plainly,
    # or one with a \u escape that could spell it. Matching the tree's name in
    # the raw bytes would miss an escaped or symlinked spelling of the path.
    with open(path, "rb") as handle:
        for line in handle:
            if b"cwd" not in line and b"\\u" not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:  # bad JSON or invalid UTF-8
                continue
            if not isinstance(entry, dict):
                continue
            payload = entry.get("payload")
            cwds = [entry.get("cwd"), payload.get("cwd") if isinstance(payload, dict) else None]
            if any(isinstance(c, str) and is_restricted(c) for c in cwds):
                return True
    return False


def transcript_is_restricted(path: Path, cwd: str | None) -> bool:
    """True when the session or any line of its transcript is under the tree."""
    return is_restricted(cwd) or is_restricted(_sniff(path)[1]) or _enters_restricted(path)


def is_restricted(cwd: str | None) -> bool:
    if not cwd:
        return False
    try:
        return Path(cwd).expanduser().resolve().is_relative_to(RESTRICTED_ROOT.resolve())
    except OSError:
        return False


def import_skip_reason(
    *, path: Path, workspaces: Iterable[str | None], declared: str | None
) -> str | None:
    """Why `activity import` skips a session, or None to import it.

    `workspaces` are what the file and the database record for the session; a
    `declared` workspace only stands in when neither knows one. With
    AXON_RESTRICTED_ROOT set, a session nobody can place is skipped: an event
    file carries no cwd, so assuming it safe would reopen the gate.
    """
    known = [w for w in workspaces if w]
    if is_restricted(str(path)) or any(is_restricted(w) for w in known):
        return "restricted cwd"
    if known:
        return None
    if declared:
        return "restricted cwd" if is_restricted(declared) else None
    if (os.environ.get("AXON_RESTRICTED_ROOT") or "").strip():
        return "unknown workspace"
    return None


async def import_transcript(
    path: Path, repo: PostgresActivityRepository | None, *, dry_run: bool = False
) -> ImportResult:
    """Import what is new in `path`. `repo=None` (dry run without a database)
    parses the whole file; `dry_run` never writes."""
    harness, cwd = _sniff(path)
    result = ImportResult(path=path, harness=harness)
    if harness is None:
        result.skipped = "not a transcript"
        return result
    if is_restricted(cwd) or _enters_restricted(path):
        result.skipped = "restricted cwd"
        return result

    prior = await repo.get_cursor(harness=harness, source_id=str(path)) if repo else None
    parse = parse_claude_code_session if harness == "claude-code" else parse_codex_session
    parsed = parse(path, prior_cursor=prior)
    if not parsed.events and prior is not None:
        return result

    result.sessions = len(parsed.sessions)
    result.redactions = sum(len(e.redactions) for e in parsed.events)
    if dry_run or repo is None:
        result.events = len(parsed.events)
        return result
    offset = int(parsed.cursor.fingerprint.rsplit(":", 1)[1])
    result.events = await repo.ingest_batch(
        parsed.sessions, parsed.events, parsed.cursor, byte_offset=offset
    )
    return result
