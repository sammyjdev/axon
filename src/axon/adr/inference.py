"""ADR inference orchestrator (dec-110 / dec-111).

Extracts the LLM call + gate pipeline + persistence routing that used
to live inside ``pb adr infer-commit``. Now callable from both the CLI
and the post-commit hook (``axon.hooks.git_event.on_commit``), closing
the wiring gap surfaced by dogfood (issue #15).

Surface:

- ``run_for_head(*, project, force, repo_root) -> InferenceResult``
  Synchronous wrapper that runs the async orchestrator.
- ``async run_for_head_async(...)`` — async variant for callers already
  inside an event loop.

Failure modes are explicit via ``InferenceStatus`` rather than silent
returns, so the hook can log diagnostically without surfacing noise.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from axon.adr.audit import record_rejection
from axon.adr.commit_context import from_head
from axon.adr.draft_pool import DraftRecord, write_draft
from axon.adr.gates import ADRPayload, GateConfig, GateOutcome, evaluate
from axon.adr.signal import detect as detect_signal

_TEMPLATE_PATH = (
    Path(__file__).parent.parent / "templates" / "adr_classifier.txt"
)


class InferenceStatus(StrEnum):
    NO_SIGNAL = "no_signal"
    NO_COMMIT = "no_commit"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_NULL = "llm_null"
    LLM_PARSE_ERROR = "llm_parse_error"
    GATE_FAILED = "gate_failed"
    SAVED_ADR = "saved_adr"


@dataclass(frozen=True)
class InferenceResult:
    """What happened during a single inference attempt."""

    status: InferenceStatus
    commit_hash: str = ""
    title: str = ""
    outcome: GateOutcome | None = field(default=None)
    error: str = ""


async def run_for_head_async(
    *,
    project: str,
    force: bool = False,
    repo_root: Path | None = None,
    db_path: Path | None = None,
    store: object | None = None,
) -> InferenceResult:
    """Run ADR inference against the current ``HEAD`` commit.

    The orchestrator is intentionally side-effecting (writes drafts,
    audit entries, or persists ADRs) but returns a structured result so
    callers can log without surfacing exceptions.

    ``store``: optional pre-initialised ``SessionStore``. When provided,
    the orchestrator reuses it (no second SQLite connection — aligns
    with dec-112's single-writer-per-process invariant). When omitted,
    a fresh store is created and closed inside this call.
    """
    root = repo_root or Path.cwd()

    try:
        commit_msg_full = _git(
            root, "log", "-1", "--pretty=%B"
        ).rstrip("\n")
        commit_msg = _git(root, "log", "-1", "--pretty=%s").strip()
        diff_stat = _git(root, "log", "-1", "--stat", "--pretty=").strip()
        diff_full = _git(
            root,
            "diff",
            "HEAD~1",
            "HEAD",
            "--",
            ":(exclude)*.lock",
            ":(exclude)*.json",
        )
    except subprocess.CalledProcessError as exc:
        return InferenceResult(
            status=InferenceStatus.NO_COMMIT, error=str(exc)
        )

    # dec-110 gate: signal required unless ``--force``
    if not force and detect_signal(commit_msg_full) is None:
        return InferenceResult(status=InferenceStatus.NO_SIGNAL)

    diff_summary = (diff_stat + "\n" + diff_full)[:3000]

    raw = await _call_llm(commit_msg, diff_summary)
    if raw is None:
        return InferenceResult(status=InferenceStatus.LLM_UNAVAILABLE)
    if not raw or raw.lower().startswith("null"):
        return InferenceResult(status=InferenceStatus.LLM_NULL)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return InferenceResult(
            status=InferenceStatus.LLM_PARSE_ERROR, error=str(exc)
        )

    payload = ADRPayload(
        title=data.get("title", commit_msg[:60]),
        context=data.get("context", ""),
        decision=data.get("decision", ""),
        rationale=data.get("rationale", ""),
    )

    try:
        commit_ctx = from_head(root)
    except subprocess.CalledProcessError:
        commit_ctx = None

    outcome: GateOutcome | None = None
    if commit_ctx is not None and commit_ctx.commit_hash:
        outcome = evaluate(
            payload, commit_ctx, GateConfig(repo_root=commit_ctx.repo_root)
        )

    if outcome is not None and not outcome.passed:
        assert commit_ctx is not None  # narrow for type checker
        draft = DraftRecord(
            commit_hash=commit_ctx.commit_hash,
            title=payload.title,
            context=payload.context,
            decision=payload.decision,
            rationale=payload.rationale,
            failed_layer=str(outcome.failed_layer) if outcome.failed_layer else "",
            failed_reason=outcome.reason or "",
            structural_mode=outcome.structural_mode,
        )
        write_draft(draft)
        record_rejection(
            commit_hash=commit_ctx.commit_hash,
            title=payload.title,
            outcome=outcome,
        )
        return InferenceResult(
            status=InferenceStatus.GATE_FAILED,
            commit_hash=commit_ctx.commit_hash,
            title=payload.title,
            outcome=outcome,
        )

    # Pipeline passed — persist to SessionStore.
    from axon.store.session_store import ADR, SessionStore

    owns_store = store is None
    if owns_store:
        store = SessionStore(db_path or _default_db_path())
        await store.init()
    try:
        adr = ADR(
            project=project,
            title=payload.title,
            context=payload.context,
            decision=payload.decision,
            rationale=payload.rationale,
        )
        await store.save_adr(adr)
    finally:
        if owns_store:
            await store.close()

    return InferenceResult(
        status=InferenceStatus.SAVED_ADR,
        commit_hash=commit_ctx.commit_hash if commit_ctx else "",
        title=payload.title,
        outcome=outcome,
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], text=True, cwd=str(root)
    )


def _adr_model() -> str:
    """Resolve which model to use for ADR classification.

    Precedence:
      1. ``AXON_ADR_MODEL`` env var (full override; useful for testing
         a specific provider/model)
      2. Active provider profile's ``classifier_model`` (dec-106)

    The classifier tier is intentionally lightweight — ADR detection is
    a classification task, not deep reasoning. Users can override via
    env to point at a heavier model when running on capable hardware.
    """
    import os

    override = os.environ.get("AXON_ADR_MODEL")
    if override:
        return override
    from axon.config.runtime import load_runtime_config
    from axon.router.profiles import get_profile

    return get_profile(load_runtime_config().provider_profile).classifier_model


async def _call_llm(commit_msg: str, diff_summary: str) -> str | None:
    """Call the ADR classifier LLM. Returns ``None`` on any failure.

    Failures include missing credentials, network errors, and provider
    errors. The hook treats this as best-effort. The model is resolved
    dynamically per call so a profile change takes effect immediately.
    """
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    prompt = template.format(
        commit_message=commit_msg, diff_summary=diff_summary
    )
    try:
        import litellm
        response = await litellm.acompletion(
            model=_adr_model(),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001 — best-effort, every error is recoverable
        return None


def _default_db_path() -> Path:
    from axon.config.runtime import load_runtime_config

    return load_runtime_config().data_root / "axon.db"
