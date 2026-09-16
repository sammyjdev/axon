import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from axon.activity.adapters.base import AdapterResult
from axon.activity.models import (
    ActivityEvent,
    ActivitySession,
    SourceCursor,
    compute_event_id,
    resolve_activity_project_from_cwd,
)
from axon.activity.sanitize import sanitize_event
from axon.memory.transcript import _message_of, _text_of


def _compute_fingerprint(path: Path, offset: int) -> str:
    try:
        with open(path, "rb") as f:
            head = f.readline()
    except OSError:
        head = b""
    file_hash = hashlib.sha256(head).hexdigest()
    return f"{file_hash}:{offset}"


def parse_codex_session(
    path: Path,
    *,
    prior_cursor: SourceCursor | None = None,
    submitted_prompt: str | None = None,
) -> AdapterResult:
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

    session_coverages: dict[str, bool] = {}
    has_user_prompt: dict[str, bool] = {}

    current_session_id = None
    turn_id = None

    with open(path, "rb") as f:
        f.seek(byte_offset)

        while True:
            line_start = f.tell()
            line = f.readline()
            if not line:
                break

            if not line.endswith(b"\n"):
                break

            byte_offset = f.tell()

            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(raw, dict):
                continue

            line_type = raw.get("type")
            payload = raw.get("payload")

            if line_type == "session_meta":
                if not isinstance(payload, dict):
                    warnings.append(f"malformed session_meta payload at {line_start}")
                    fallback_id = f"unknown-{file_hash}"
                    current_session_id = fallback_id
                    if fallback_id not in sessions_dict:
                        sessions_dict[fallback_id] = ActivitySession(
                            session_id=fallback_id,
                            harness="codex",
                            source_id=f"{path}::{fallback_id}",
                            project=None,
                            workspace=None,
                            parent_session_id=None,
                            status="observed",
                            coverage="unsupported-format",
                        )
                        session_coverages[fallback_id] = True
                    continue

                sess_id = payload.get("session_id") or payload.get("id")
                if not sess_id or not isinstance(sess_id, str):
                    warnings.append(
                        f"malformed session_meta at {line_start}: missing session_id/id"
                    )
                    fallback_id = f"unknown-{file_hash}"
                    current_session_id = fallback_id
                    if fallback_id not in sessions_dict:
                        sessions_dict[fallback_id] = ActivitySession(
                            session_id=fallback_id,
                            harness="codex",
                            source_id=f"{path}::{fallback_id}",
                            project=None,
                            workspace=None,
                            parent_session_id=None,
                            status="observed",
                            coverage="unsupported-format",
                        )
                        session_coverages[fallback_id] = True
                    continue

                current_session_id = str(sess_id)
                current_cwd = payload.get("cwd")
                cwd_str = str(current_cwd) if current_cwd else None
                if current_session_id not in sessions_dict:
                    sessions_dict[current_session_id] = ActivitySession(
                        session_id=current_session_id,
                        harness="codex",
                        source_id=f"{path}::{current_session_id}",
                        project=resolve_activity_project_from_cwd(cwd_str),
                        workspace=cwd_str,
                        parent_session_id=None,
                        status="observed",
                        coverage="complete-observable",
                    )
                    session_coverages[current_session_id] = False
                    has_user_prompt[current_session_id] = False
                continue

            if not current_session_id:
                continue

            occurred_at_str = raw.get("timestamp")
            try:
                if isinstance(occurred_at_str, str):
                    occurred_at_str = occurred_at_str.replace("Z", "+00:00")
                    occurred_at = datetime.fromisoformat(occurred_at_str)
                else:
                    occurred_at = datetime.now(UTC)
            except ValueError:
                occurred_at = datetime.now(UTC)

            ingested_at = datetime.now(UTC)

            if line_type in ("event_msg", "turn_context"):
                if isinstance(payload, dict):
                    tid = payload.get("turn_id")
                    if isinstance(tid, str):
                        turn_id = tid
                continue

            elif line_type == "response_item":
                if not isinstance(payload, dict):
                    continue

                payload_type = payload.get("type")

                msg = _message_of(raw)
                if msg is not None:
                    role = msg.get("role")
                    if role in ("user", "developer"):
                        text = _text_of(msg.get("content", "")).strip()
                        if text:
                            has_user_prompt[current_session_id] = True

                    event_source_id = f"{path}::{current_session_id}::msg::{line_start}"
                    sanitized, redactions, coverage = sanitize_event(msg)

                    events.append(
                        ActivityEvent(
                            event_id=compute_event_id("codex", event_source_id),
                            harness="codex",
                            source_id=event_source_id,
                            session_id=current_session_id,
                            turn_id=turn_id,
                            call_id=None,
                            parent_session_id=None,
                            occurred_at=occurred_at,
                            ingested_at=ingested_at,
                            kind="message",
                            content=sanitized,
                            coverage=coverage,
                            redactions=redactions,
                        )
                    )

                elif payload_type == "function_call":
                    call_id = payload.get("call_id")
                    if isinstance(call_id, str):
                        event_source_id = (
                            f"{path}::{current_session_id}::tool_call::{call_id}::{line_start}"
                        )
                        sanitized, redactions, coverage = sanitize_event(payload)
                        events.append(
                            ActivityEvent(
                                event_id=compute_event_id("codex", event_source_id),
                                harness="codex",
                                source_id=event_source_id,
                                session_id=current_session_id,
                                turn_id=turn_id,
                                call_id=call_id,
                                parent_session_id=None,
                                occurred_at=occurred_at,
                                ingested_at=ingested_at,
                                kind="tool_call",
                                content=sanitized,
                                coverage=coverage,
                                redactions=redactions,
                            )
                        )

                elif payload_type == "function_call_output":
                    call_id = payload.get("call_id")
                    if isinstance(call_id, str):
                        event_source_id = (
                            f"{path}::{current_session_id}::tool_result::{call_id}::{line_start}"
                        )
                        sanitized, redactions, coverage = sanitize_event(payload)
                        events.append(
                            ActivityEvent(
                                event_id=compute_event_id("codex", event_source_id),
                                harness="codex",
                                source_id=event_source_id,
                                session_id=current_session_id,
                                turn_id=turn_id,
                                call_id=call_id,
                                parent_session_id=None,
                                occurred_at=occurred_at,
                                ingested_at=ingested_at,
                                kind="tool_result",
                                content=sanitized,
                                coverage=coverage,
                                redactions=redactions,
                            )
                        )

            elif line_type == "token_usage_record":
                if not isinstance(payload, dict):
                    continue

                event_source_id = f"{path}::{current_session_id}::usage::{line_start}"
                sanitized, redactions, coverage = sanitize_event(payload)

                rec_turn_id = payload.get("turn_id", turn_id)
                if not isinstance(rec_turn_id, str):
                    rec_turn_id = turn_id

                events.append(
                    ActivityEvent(
                        event_id=compute_event_id("codex", event_source_id),
                        harness="codex",
                        source_id=event_source_id,
                        session_id=current_session_id,
                        turn_id=rec_turn_id,
                        call_id=None,
                        parent_session_id=None,
                        occurred_at=occurred_at,
                        ingested_at=ingested_at,
                        kind="usage",
                        content=sanitized,
                        coverage=coverage,
                        redactions=redactions,
                    )
                )

    for sid, has_prompt in has_user_prompt.items():
        if not has_prompt:
            if submitted_prompt is not None:
                event_source_id = f"{path}::{sid}::prompt::submitted"
                sanitized_text, redactions, coverage = sanitize_event({"text": submitted_prompt})
                events.append(
                    ActivityEvent(
                        event_id=compute_event_id("codex", event_source_id),
                        harness="codex",
                        source_id=event_source_id,
                        session_id=sid,
                        turn_id=None,
                        call_id=None,
                        parent_session_id=None,
                        occurred_at=datetime.now(UTC),
                        ingested_at=datetime.now(UTC),
                        kind="prompt",
                        content=sanitized_text,
                        coverage="partial-observable",
                        redactions=redactions,
                    )
                )
            else:
                warnings.append("session prompt is unobserved")
                session_coverages[sid] = True

    for s_id, has_unsupported in session_coverages.items():
        if has_unsupported:
            sessions_dict[s_id].coverage = "partial-observable"

    primary_session_id = next(iter(sessions_dict.keys())) if sessions_dict else ""

    cursor = SourceCursor(
        session_id=primary_session_id,
        harness="codex",
        source_id=str(path),
        fingerprint=_compute_fingerprint(path, byte_offset),
    )

    return AdapterResult(
        events=events,
        sessions=list(sessions_dict.values()),
        cursor=cursor,
        warnings=warnings,
    )
