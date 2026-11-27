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

import contextlib
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_GIT_TIMEOUT_S = 10
# Variables that point git somewhere other than the directory asked about. Git
# hooks export some of them; inherited, they make the proof answer for the wrong
# repository, or for none.
_GIT_LOCATION_VARS = (
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_WORK_TREE",
)
# Config injected for one process tree (`git -c` exports it to hooks). A later
# plain `git add -A` does not have it, so the proof must not either.
_GIT_CONFIG_VARS = ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS")
_GIT_CONFIG_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


class _Unproven(Exception):
    """One git proof failed; the message is the reason."""


class SpoolNotIgnoredError(RuntimeError):
    """Git could stage the spool, or that could not be ruled out: nothing is written."""

    def __init__(self, directory: Path, reason: str) -> None:
        super().__init__(f"spool not ignored: {directory}: {reason}")
        self.directory = directory
        self.reason = reason


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


def _pending_paths() -> PendingPaths:
    """Resolve the pending/quarantine layout under the AXON data root."""
    from axon.config.data_root import data_root

    root = data_root()
    return PendingPaths(
        pending_dir=root / "pending",
        quarantine_dir=root / "pending-quarantine",
        quarantine_log=root / "quarantine.jsonl",
    )


def _warnings_log() -> Path:
    """Return the path to the capture-warnings log."""
    from axon.config.data_root import data_root

    return data_root() / "capture-warnings.jsonl"


def _run_git(parent: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _GIT_LOCATION_VARS + _GIT_CONFIG_VARS
        and not k.startswith(_GIT_CONFIG_PREFIXES)
    }
    env["LC_ALL"] = "C"  # "not a git repository" is matched in English
    try:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(parent), *args],  # noqa: S607
            cwd=None,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            shell=False,
            env=env,
        )
    except FileNotFoundError:
        raise _Unproven("git not found") from None
    except OSError as exc:
        raise _Unproven(f"git could not run: {type(exc).__name__}: {exc}") from None
    except subprocess.TimeoutExpired:
        raise _Unproven(f"git timed out after {_GIT_TIMEOUT_S}s") from None


def _in_work_tree(parent: Path) -> bool:
    """Only a plain yes or no counts; any other answer is unproven."""
    if not parent.is_dir():
        raise _Unproven(f"{parent} is not a directory")
    probe = _run_git(parent, "rev-parse", "--is-inside-work-tree")
    answer = probe.stdout.strip()
    if probe.returncode == 0 and answer in ("true", "false"):
        return answer == "true"
    if probe.returncode == 128 and "not a git repository" in probe.stderr:
        return False
    raise _Unproven(f"git rev-parse failed: {probe.stderr.strip() or probe.returncode}")


def _prove_dir_ignored(parent: Path, name: str) -> bool:
    """Raise unless git provably cannot stage anything under ``parent/name``.

    Returns False when ``parent`` is outside any work tree, True when it is
    inside one that ignores ``name``.
    """
    if not _in_work_tree(parent):
        return False
    # The child is named relative to -C: a relative data root must not be doubled.
    tracked = _run_git(parent, "ls-files", "--", name)
    if tracked.returncode != 0:
        raise _Unproven(f"git ls-files failed: {tracked.stderr.strip() or tracked.returncode}")
    if tracked.stdout.strip():
        raise _Unproven("files in the spool directory are tracked by git")
    if _run_git(parent, "check-ignore", "-q", f"{name}/probe.json").returncode != 0:
        raise _Unproven("git does not ignore the spool directory")
    return True


def spool_refusals_path(data_root: Path | None = None) -> Path:
    """The refusal record: under the engine data root, never beside the spool,
    which may be unwritable in the very case being reported."""
    if data_root is None:
        from axon.config.runtime import load_runtime_config

        data_root = load_runtime_config().data_root
    return data_root / "spool-refusals.json"


def read_spool_refusals(record: Path) -> dict | None:
    """The refusal record, None without one; OSError or ValueError if unreadable."""
    if not record.exists():
        return None
    entry = json.loads(record.read_text(encoding="utf-8"))
    if not (
        isinstance(entry, dict)
        and isinstance(entry.get("count"), int)
        and {"last_at", "last_dir", "last_reason"} <= entry.keys()
    ):
        raise ValueError("not a refusal record")
    return entry


def record_spool_refusal(directory: Path, reason: str) -> None:
    """Count one refused spool write. Paths and error text only, never payload."""
    tmp: Path | None = None
    try:
        record = spool_refusals_path()
        previous = None
        with contextlib.suppress(OSError, ValueError):  # unreadable: start over
            previous = read_spool_refusals(record)
        count = previous["count"] if previous else 0
        entry = {
            "count": count + 1,
            "last_at": datetime.now(UTC).isoformat(),
            "last_dir": os.path.abspath(directory),
            "last_reason": reason[:500],
        }
        record.parent.mkdir(parents=True, exist_ok=True)
        tmp = record.with_name(f"spool-refusals.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, record)
    except Exception as exc:  # best effort: the refusal itself must still be raised
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
        print(f"[axon] could not record spool refusal: {exc}", file=sys.stderr)


def _refusal(directory: Path, reason: str, write_error: str = "") -> SpoolNotIgnoredError:
    """Record the refusal, then build the error the caller raises."""
    if write_error:
        reason = f"{reason}; ignore rule not written ({write_error})"
    reason = " ".join(reason.splitlines())  # hooks report a refusal on one line
    record_spool_refusal(directory, reason)
    return SpoolNotIgnoredError(directory, reason)


def assert_spool_path_ignored(path: Path) -> None:
    """Raise unless git ignores this exact payload name.

    A negation such as ``!pending/sess-*.json`` leaves the directory probe
    ignored and the real file stageable.
    """
    directory = path.parent
    parent = directory.parent
    try:
        if not _in_work_tree(parent):
            return
        name = f"{directory.name}/{path.name}"
        if _run_git(parent, "check-ignore", "-q", name).returncode != 0:
            raise _Unproven(f"git does not ignore {path.name}")
    except _Unproven as exc:
        raise _refusal(directory, str(exc)) from None


def _ensure_spool_ignored(paths: PendingPaths) -> None:
    """Write the spool ignore rules, then prove git ignores the spool or raise."""
    dirs = [paths.pending_dir, paths.quarantine_dir]
    by_parent: dict[Path, list[Path]] = {}
    for d in dirs:
        by_parent.setdefault(d.parent, []).append(d)

    for parent, children in by_parent.items():
        write_error = ""
        tmp: Path | None = None
        try:
            parent.mkdir(parents=True, exist_ok=True)
            ignore_file = parent / ".gitignore"
            content = ""
            existing_lines: list[str] = []
            if ignore_file.is_file():
                content = ignore_file.read_text(encoding="utf-8")
                existing_lines = [
                    line.strip()
                    for line in content.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]

            to_add: list[str] = []
            for child in children:
                rule = f"{child.name}/"
                rule_clean = child.name
                already_present = any(
                    line in (
                        "*",
                        "/*",
                        rule,
                        f"/{rule}",
                        rule_clean,
                        f"/{rule_clean}",
                        f"{rule}*",
                        f"/{rule}*",
                        f"{rule}**",
                        f"/{rule}**",
                    )
                    for line in existing_lines + to_add
                )
                if not already_present:
                    to_add.append(rule)

            if to_add:
                prefix = "\n" if (content and not content.endswith("\n")) else ""
                tmp = ignore_file.with_name(f".gitignore.{uuid.uuid4().hex[:8]}.tmp")
                tmp.write_text(content + prefix + "\n".join(to_add) + "\n", encoding="utf-8")
                os.replace(tmp, ignore_file)
        except OSError as exc:
            write_error = f"{type(exc).__name__}: {exc}"
            if tmp is not None:
                with contextlib.suppress(OSError):
                    tmp.unlink(missing_ok=True)

        inside = False
        for child in children:
            try:
                inside = _prove_dir_ignored(parent, child.name)
            except _Unproven as exc:
                raise _refusal(child, str(exc), write_error) from None
        if write_error and inside:
            print(
                f"[axon] spool ignore rule not written ({write_error}); "
                f"git already ignores {children[0]}",
                file=sys.stderr,
            )


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
    _ensure_spool_ignored(paths)
    paths.pending_dir.mkdir(parents=True, exist_ok=True)
    ts_ns = time.time_ns()
    safe_hash = commit_hash or "nohash"
    # uuid suffix guarantees a unique path even when two writes land in the same
    # clock tick (time_ns() is coarse on Windows) or come from concurrent hook
    # processes - otherwise the second os.replace would silently overwrite the
    # first pending payload (data loss). Drain orders by st_mtime_ns, not name,
    # so the extra suffix does not affect replay order.
    final = paths.pending_dir / f"{safe_hash}-{ts_ns}-{uuid.uuid4().hex[:8]}.json"
    assert_spool_path_ignored(final)
    tmp = final.with_suffix(".json.tmp")
    assert_spool_path_ignored(tmp)  # the payload lands under this name first
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, final)
    except BaseException:  # drain never reads a leftover temp file
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    return final


async def quarantine_invalid(
    src: Path,
    *,
    reason: str,
    paths: PendingPaths,
) -> Path:
    """Move ``src`` into the quarantine dir and append a structured log entry."""
    _ensure_spool_ignored(paths)
    paths.quarantine_dir.mkdir(parents=True, exist_ok=True)
    ts_ns = time.time_ns()
    dest = paths.quarantine_dir / f"{src.name}.{ts_ns}"
    assert_spool_path_ignored(dest)
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
