"""Audit log for ADR rejections and weak-passes (dec-111).

Every gate rejection and every passing-but-low-density evaluation
appends a structured line to ``.axon/adr-rejected.jsonl``. The CLI
``pb adr audit`` reads back this log to surface candidates that may
need manual review.

JSONL is intentionally simple: one record per line, no rotation logic
beyond a size cap warning in ``pb doctor``. Format is stable and
machine-parseable.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from axon.adr.gates import GateOutcome

EventKind = Literal["rejection", "weak_pass"]


def _audit_log_path() -> Path:
    root = Path(os.environ.get("AXON_DATA_ROOT", ".axon"))
    return root / "adr-rejected.jsonl"


def record_rejection(
    *,
    commit_hash: str,
    title: str,
    outcome: GateOutcome,
    log_path: Path | None = None,
) -> None:
    """Append a rejection record to the audit log."""
    target = log_path or _audit_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "kind": "rejection",
        "commit_hash": commit_hash,
        "title": title,
        "layer": str(outcome.failed_layer) if outcome.failed_layer else "",
        "reason": outcome.reason or "",
        "structural_mode": outcome.structural_mode,
        "details": _safe_details(outcome.details),
        "ts": datetime.now(UTC).isoformat(),
    }
    _append(target, entry)


def record_weak_pass(
    *,
    commit_hash: str,
    title: str,
    outcome: GateOutcome,
    log_path: Path | None = None,
) -> None:
    """Append a weak-pass entry — ADR passed but density was borderline."""
    target = log_path or _audit_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "kind": "weak_pass",
        "commit_hash": commit_hash,
        "title": title,
        "structural_mode": outcome.structural_mode,
        "details": _safe_details(outcome.details),
        "ts": datetime.now(UTC).isoformat(),
    }
    _append(target, entry)


def read_audit(
    *,
    since: datetime | None = None,
    kinds: tuple[EventKind, ...] = ("rejection", "weak_pass"),
    log_path: Path | None = None,
) -> list[dict]:
    """Return audit entries optionally filtered by timestamp and kind."""
    target = log_path or _audit_log_path()
    if not target.exists():
        return []
    out: list[dict] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("kind") not in kinds:
            continue
        if since is not None:
            try:
                ts = datetime.fromisoformat(entry.get("ts", ""))
            except ValueError:
                continue
            if ts < since:
                continue
        out.append(entry)
    return out


def _append(path: Path, entry: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _safe_details(details: dict[str, object]) -> dict[str, object]:
    """Strip values that are not JSON-serialisable (e.g. Path, frozenset)."""
    out: dict[str, object] = {}
    for k, v in details.items():
        try:
            json.dumps(v)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = str(v)
    return out
