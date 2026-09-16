import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from axon.activity.adapters.base import AdapterResult
from axon.activity.models import ActivityEvent, ActivitySession, SourceCursor, compute_event_id
from axon.activity.sanitize import sanitize_event


def _compute_fingerprint(path: Path, offset: int) -> str:
    """Compute a stable fingerprint for a Claude Code log file.

    The fingerprint is a SHA-256 hash of up to the first 256 bytes of the file,
    combined with the byte offset, in the format: <hash>:<offset>

    This ensures we can detect if the file was replaced (the hash of the first
    256 bytes will differ), and also allows us to store the advanced byte_offset
    within the SourceCursor.fingerprint field.
    """
    try:
        with open(path, "rb") as f:
            head = f.readline()
    except OSError:
        head = b""
    file_hash = hashlib.sha256(head).hexdigest()
    return f"{file_hash}:{offset}"


def parse_claude_code_session(
    path: Path, *, prior_cursor: SourceCursor | None = None
) -> AdapterResult:
    """Incrementally parse a Claude Code session JSONL file into ActivityEvents."""
    events: list[ActivityEvent] = []
    sessions_dict: dict[str, ActivitySession] = {}
    warnings: list[str] = []

    byte_offset = 0
    file_hash = ""

    try:
        with open(path, "rb") as f:
            head = f.readline()
            file_hash = hashlib.sha256(head).hexdigest()
    except OSError:
        pass

    if prior_cursor and prior_cursor.fingerprint:
        parts = prior_cursor.fingerprint.split(":")
        if len(parts) == 2 and parts[0] == file_hash:
            try:
                byte_offset = int(parts[1])
            except ValueError:
                pass

    if byte_offset < 0:
        byte_offset = 0

    session_coverages: dict[str, bool] = {}  # true if any event was unsupported

    with open(path, "rb") as f:
        f.seek(byte_offset)

        while True:
            line_start = f.tell()
            line = f.readline()
            if not line:
                break

            if not line.endswith(b"\n"):
                # Partial trailing line, do not advance byte_offset past its start
                # We simply stop here, leaving byte_offset at line_start
                break

            byte_offset = f.tell()

            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(raw, dict):
                continue

            line_type = raw.get("type")
            if line_type not in ("user", "assistant", "cost-state"):
                continue

            session_id = raw.get("sessionId")
            if not session_id or not isinstance(session_id, str):
                continue

            if session_id not in sessions_dict:
                parent_session_id = raw.get("parentSessionId")
                sessions_dict[session_id] = ActivitySession(
                    session_id=session_id,
                    harness="claude-code",
                    source_id=f"{path}::{session_id}",
                    project=None,
                    workspace=raw.get("cwd") if isinstance(raw.get("cwd"), str) else None,
                    parent_session_id=parent_session_id
                    if isinstance(parent_session_id, str)
                    else None,
                    status="observed",
                    coverage="complete-observable",
                )
                session_coverages[session_id] = False

            occurred_at_str = raw.get("timestamp")
            try:
                if isinstance(occurred_at_str, str):
                    # Python 3.11 datetime.fromisoformat handles Z
                    occurred_at_str = occurred_at_str.replace("Z", "+00:00")
                    occurred_at = datetime.fromisoformat(occurred_at_str)
                else:
                    occurred_at = datetime.now(UTC)
            except ValueError:
                occurred_at = datetime.now(UTC)

            ingested_at = datetime.now(UTC)

            if line_type in ("user", "assistant"):
                turn_id = raw.get("uuid")
                if not turn_id or not isinstance(turn_id, str):
                    continue

                message = raw.get("message")
                if (
                    not isinstance(message, dict)
                    or "content" not in message
                    or message.get("content") is None
                ):
                    warnings.append(
                        f"unsupported user/assistant line at byte offset {line_start}: "
                        "missing or null message content"
                    )
                    session_coverages[session_id] = True

                    event_source_id = f"{path}::{turn_id}::unsupported"
                    events.append(
                        ActivityEvent(
                            event_id=compute_event_id("claude-code", event_source_id),
                            harness="claude-code",
                            source_id=event_source_id,
                            session_id=session_id,
                            turn_id=turn_id,
                            call_id=None,
                            parent_session_id=sessions_dict[session_id].parent_session_id,
                            occurred_at=occurred_at,
                            ingested_at=ingested_at,
                            kind="message",
                            content={},
                            coverage="unsupported-format",
                            redactions=[],
                        )
                    )
                    continue

                content = message.get("content")
                if isinstance(content, str):
                    blocks = [{"type": "text", "text": content}]
                elif isinstance(content, list):
                    blocks = content
                else:
                    warnings.append(
                        f"unsupported user/assistant line at byte offset {line_start}: "
                        "content is neither string nor list"
                    )
                    session_coverages[session_id] = True
                    event_source_id = f"{path}::{turn_id}::unsupported"
                    events.append(
                        ActivityEvent(
                            event_id=compute_event_id("claude-code", event_source_id),
                            harness="claude-code",
                            source_id=event_source_id,
                            session_id=session_id,
                            turn_id=turn_id,
                            call_id=None,
                            parent_session_id=sessions_dict[session_id].parent_session_id,
                            occurred_at=occurred_at,
                            ingested_at=ingested_at,
                            kind="message",
                            content={},
                            coverage="unsupported-format",
                            redactions=[],
                        )
                    )
                    continue

                for idx, block in enumerate(blocks):
                    if not isinstance(block, dict):
                        continue
                    b_type = block.get("type")
                    if b_type == "text":
                        event_source_id = f"{path}::{turn_id}::text::{idx}"
                        sanitized, redactions, coverage = sanitize_event(block)
                        events.append(
                            ActivityEvent(
                                event_id=compute_event_id("claude-code", event_source_id),
                                harness="claude-code",
                                source_id=event_source_id,
                                session_id=session_id,
                                turn_id=turn_id,
                                call_id=None,
                                parent_session_id=sessions_dict[session_id].parent_session_id,
                                occurred_at=occurred_at,
                                ingested_at=ingested_at,
                                kind="message",
                                content=sanitized,
                                coverage=coverage,
                                redactions=redactions,
                            )
                        )
                    elif b_type == "tool_use":
                        call_id = block.get("id")
                        if isinstance(call_id, str):
                            event_source_id = f"{path}::{turn_id}::tool_use::{call_id}"
                            sanitized, redactions, coverage = sanitize_event(block)
                            events.append(
                                ActivityEvent(
                                    event_id=compute_event_id("claude-code", event_source_id),
                                    harness="claude-code",
                                    source_id=event_source_id,
                                    session_id=session_id,
                                    turn_id=turn_id,
                                    call_id=call_id,
                                    parent_session_id=sessions_dict[session_id].parent_session_id,
                                    occurred_at=occurred_at,
                                    ingested_at=ingested_at,
                                    kind="tool_call",
                                    content=sanitized,
                                    coverage=coverage,
                                    redactions=redactions,
                                )
                            )
                    elif b_type == "tool_result":
                        call_id = block.get("tool_use_id")
                        if isinstance(call_id, str):
                            event_source_id = f"{path}::{turn_id}::tool_result::{call_id}"
                            sanitized, redactions, coverage = sanitize_event(block)
                            events.append(
                                ActivityEvent(
                                    event_id=compute_event_id("claude-code", event_source_id),
                                    harness="claude-code",
                                    source_id=event_source_id,
                                    session_id=session_id,
                                    turn_id=turn_id,
                                    call_id=call_id,
                                    parent_session_id=sessions_dict[session_id].parent_session_id,
                                    occurred_at=occurred_at,
                                    ingested_at=ingested_at,
                                    kind="tool_result",
                                    content=sanitized,
                                    coverage=coverage,
                                    redactions=redactions,
                                )
                            )
            elif line_type == "cost-state":
                line_hash = hashlib.sha256(line).hexdigest()
                event_source_id = f"{path}::{session_id}::cost::{line_hash}"
                model_usage = raw.get("modelUsage", {})
                sanitized, redactions, coverage = sanitize_event(model_usage)
                events.append(
                    ActivityEvent(
                        event_id=compute_event_id("claude-code", event_source_id),
                        harness="claude-code",
                        source_id=event_source_id,
                        session_id=session_id,
                        turn_id=None,
                        call_id=None,
                        parent_session_id=sessions_dict[session_id].parent_session_id,
                        occurred_at=occurred_at,
                        ingested_at=ingested_at,
                        kind="usage",
                        content=sanitized,
                        coverage=coverage,
                        redactions=redactions,
                    )
                )

    # Update session coverages
    for s_id, has_unsupported in session_coverages.items():
        if has_unsupported:
            sessions_dict[s_id].coverage = "partial-observable"

    primary_session_id = next(iter(sessions_dict.keys())) if sessions_dict else ""

    cursor = SourceCursor(
        session_id=primary_session_id,
        harness="claude-code",
        source_id=str(path),
        fingerprint=_compute_fingerprint(path, byte_offset),
    )

    return AdapterResult(
        events=events,
        sessions=list(sessions_dict.values()),
        cursor=cursor,
        warnings=warnings,
    )
