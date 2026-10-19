"""Import agent transcripts (Claude Code, Codex) into the activity history.

One entry point for the SessionEnd hook and for `axon activity backfill`: the
harness is told by the file's content, the cursor makes a re-import cheap, and
a source's sessions, events and cursor land in one transaction. When the
database is down nothing is lost: the cursor does not move, and the next
import reads from the same offset - the transcript itself is the spool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from axon.activity.adapters.claude_code import parse_claude_code_session
from axon.activity.adapters.codex import parse_codex_session
from axon.activity.repository import PostgresActivityRepository

Harness = Literal["claude-code", "codex"]

# Off-limits tree (employer work): a session there is skipped on its cwd alone.
RESTRICTED_ROOT = Path.home() / "dev" / "afya"
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
            except json.JSONDecodeError:
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


def is_restricted(cwd: str | None) -> bool:
    if not cwd:
        return False
    try:
        return Path(cwd).resolve().is_relative_to(RESTRICTED_ROOT.resolve())
    except OSError:
        return False


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
    if is_restricted(cwd):
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
