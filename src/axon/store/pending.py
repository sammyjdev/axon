"""Pending directory + drain + quarantine (dec-112).

When a SessionStore write cannot succeed within the retry budget, the
payload is serialised to ``.axon/pending/{commit_hash}-{ts_ns}.json``.
A subsequent drain pass enumerates the directory in chronological order
and replays each payload through a caller-supplied sink. Malformed files
are moved to a quarantine directory with a structured log entry.

The contract is intentionally narrow:

- Unique paths per write eliminate write/write races.
- Atomic rename guarantees no half-written reader sees a partial file.
- Per-file try/except in drain ensures one bad payload cannot block the
  loop.
- Idempotent replay: callers should treat the payload's natural key as
  the deduplication anchor.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class PendingPaths:
    """Filesystem locations for the pending subsystem."""

    pending_dir: Path
    quarantine_dir: Path
    quarantine_log: Path


@dataclass
class DrainResult:
    """Summary of a drain pass."""

    processed: int = 0
    quarantined: int = 0
    retried: int = 0
    errors: list[str] = field(default_factory=list)


async def write_pending(
    *,
    payload: dict,
    commit_hash: str,
    paths: PendingPaths,
) -> Path:
    """Atomically write ``payload`` to ``pending/{commit_hash}-{ts_ns}.json``.

    Uses write-then-rename so concurrent readers never see a partial file.
    Returns the final path.
    """
    paths.pending_dir.mkdir(parents=True, exist_ok=True)
    ts_ns = time.time_ns()
    safe_hash = commit_hash or "nohash"
    final = paths.pending_dir / f"{safe_hash}-{ts_ns}.json"
    tmp = final.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, final)
    return final


async def quarantine_invalid(
    src: Path,
    *,
    reason: str,
    paths: PendingPaths,
) -> Path:
    """Move ``src`` into the quarantine dir and append a structured log entry."""
    paths.quarantine_dir.mkdir(parents=True, exist_ok=True)
    ts_ns = time.time_ns()
    dest = paths.quarantine_dir / f"{src.name}.{ts_ns}"
    os.replace(src, dest)
    entry = {
        "original_path": str(src),
        "quarantined_to": str(dest),
        "reason": reason,
        "ts": datetime.now(UTC).isoformat(),
    }
    paths.quarantine_log.parent.mkdir(parents=True, exist_ok=True)
    with paths.quarantine_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return dest


async def drain_pending(
    paths: PendingPaths,
    *,
    sink: Callable[[dict], Awaitable[None]],
    is_retryable: Callable[[Exception], bool] = lambda _e: False,
) -> DrainResult:
    """Process every file in ``pending_dir`` chronologically.

    For each file:
      - Parse JSON. On failure, quarantine and continue.
      - Call ``sink(payload)``. On a retryable error, leave the file in
        place (next drain retries). On any other error, quarantine.
      - On success, delete the file.
    """
    result = DrainResult()
    if not paths.pending_dir.exists():
        return result

    files = sorted(
        paths.pending_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime_ns,
    )
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError, OSError) as exc:
            await quarantine_invalid(
                path, reason=f"{type(exc).__name__}: {exc}", paths=paths
            )
            result.quarantined += 1
            continue

        try:
            await sink(payload)
        except Exception as exc:  # noqa: BLE001 — predicate decides retryable
            if is_retryable(exc):
                result.retried += 1
                # Leave in place for next drain
                continue
            await quarantine_invalid(
                path, reason=f"{type(exc).__name__}: {exc}", paths=paths
            )
            result.quarantined += 1
            result.errors.append(f"{path.name}: {exc}")
            continue

        try:
            path.unlink()
            result.processed += 1
        except OSError as exc:
            result.errors.append(f"unlink {path.name}: {exc}")

    return result


def emit_capture_warning(
    warnings_log: Path,
    *,
    kind: str,
    commit_hash: str,
    reason: str,
) -> None:
    """Append a structured warning to ``capture-warnings.jsonl`` (sync).

    Called from the SessionStore fallback path. Sync because the call site
    is already inside an async function but the I/O is trivial.
    """
    warnings_log.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "kind": kind,
        "commit_hash": commit_hash,
        "reason": reason,
        "ts": datetime.now(UTC).isoformat(),
    }
    with warnings_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
