import hashlib
from typing import TYPE_CHECKING

from axon.activity.adapters.base import AdapterResult
from axon.activity.models import (
    ActivityEvent,
    ActivitySession,
    SourceCursor,
    compute_event_id,
    resolve_activity_project_from_cwd,
)
from axon.activity.sanitize import sanitize_event

if TYPE_CHECKING:
    from axon.activity.agy_runner import AgyRunResult


def build_agy_session(
    result: "AgyRunResult", *, source_id: str, workspace_diff: list[str] | None = None
) -> AdapterResult:
    """Turn one captured AgyRunResult into ActivityEvents + one ActivitySession.

    Rules:
    - `harness='agy'`.
    - One synthetic session_id derived deterministically from `source_id`.
    - One event, kind='prompt', content=sanitized `result.prompt`.
    - One event, kind='terminal_output', content=sanitized `result.output`,
      including explicit 'unavailable' markers for internal_actions and usage.
    - outcome is set correctly based on exit code / timeout.
    - ActivitySession.coverage = 'partial-observable' UNCONDITIONALLY.
    - Optional workspace_diff event.
    """
    events: list[ActivityEvent] = []

    session_id = hashlib.sha256(source_id.encode("utf-8")).hexdigest()

    workspace_str = str(result.workspace)
    session = ActivitySession(
        session_id=session_id,
        harness="agy",
        source_id=source_id,
        project=resolve_activity_project_from_cwd(workspace_str),
        workspace=workspace_str,
        parent_session_id=None,
        status="observed",
        coverage="partial-observable",
    )

    if result.timed_out:
        outcome = "timed_out"
    elif result.exit_code == 0:
        outcome = "process_succeeded"
    else:
        outcome = "process_failed"

    prompt_content = {"prompt": result.prompt}
    prompt_sanitized, prompt_redactions, prompt_coverage = sanitize_event(prompt_content)
    prompt_event_id = compute_event_id("agy", f"{source_id}::prompt")
    events.append(
        ActivityEvent(
            event_id=prompt_event_id,
            harness="agy",
            source_id=f"{source_id}::prompt",
            session_id=session_id,
            turn_id=None,
            call_id=None,
            parent_session_id=None,
            occurred_at=result.started_at,
            ingested_at=result.finished_at,
            kind="prompt",
            content=prompt_sanitized,
            coverage=prompt_coverage,
            redactions=prompt_redactions,
            outcome=None,
        )
    )

    output_content = {
        "output": result.output,
        "internal_actions": "unavailable",
        "usage": "unavailable",
    }
    output_sanitized, output_redactions, output_coverage = sanitize_event(output_content)
    output_event_id = compute_event_id("agy", f"{source_id}::terminal_output")
    events.append(
        ActivityEvent(
            event_id=output_event_id,
            harness="agy",
            source_id=f"{source_id}::terminal_output",
            session_id=session_id,
            turn_id=None,
            call_id=None,
            parent_session_id=None,
            occurred_at=result.finished_at,
            ingested_at=result.finished_at,
            kind="terminal_output",
            content=output_sanitized,
            coverage=output_coverage,
            redactions=output_redactions,
            outcome=outcome,
        )
    )

    if workspace_diff is not None:
        diff_content = {"files": workspace_diff}
        diff_sanitized, diff_redactions, diff_coverage = sanitize_event(diff_content)
        diff_event_id = compute_event_id("agy", f"{source_id}::workspace_diff")
        events.append(
            ActivityEvent(
                event_id=diff_event_id,
                harness="agy",
                source_id=f"{source_id}::workspace_diff",
                session_id=session_id,
                turn_id=None,
                call_id=None,
                parent_session_id=None,
                occurred_at=result.finished_at,
                ingested_at=result.finished_at,
                kind="workspace_diff",
                content=diff_sanitized,
                coverage=diff_coverage,
                redactions=diff_redactions,
                outcome=None,
            )
        )

    cursor = SourceCursor(
        session_id=session_id,
        harness="agy",
        source_id=source_id,
        fingerprint="",
    )

    return AdapterResult(
        events=events,
        sessions=[session],
        cursor=cursor,
        warnings=[],
    )
