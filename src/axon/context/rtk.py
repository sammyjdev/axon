from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path


class RTKError(RuntimeError):
    pass


def _bootstrap_binary() -> Path:
    """Default install location written by `axon rtk-init` (the rtkx bootstrap)."""
    exe = "rtkx.exe" if os.name == "nt" else "rtkx"
    return Path.home() / ".axon" / "bin" / exe


def _usable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


@lru_cache(maxsize=1)
def rtk_binary_path() -> str | None:
    """Resolve the rtkx/rtk binary.

    Order: AXON_RTK_BIN -> ~/.axon/bin/rtkx (bootstrap) -> PATH (rtkx, then rtk).
    Raw path candidates are validated on disk; PATH lookups via ``shutil.which``
    already guarantee an existing, executable file.
    """
    explicit = os.environ.get("AXON_RTK_BIN")
    if explicit:
        path = Path(explicit).expanduser()
        if _usable(path):
            return str(path)

    boot = _bootstrap_binary()
    if _usable(boot):
        return str(boot)

    # Prefer our fork binary (rtkx); fall back to upstream rtk for compatibility.
    for name in ("rtkx", "rtk"):
        found = shutil.which(name)
        if found:
            return found

    return None


def rtk_installed() -> bool:
    return rtk_binary_path() is not None


def compress_text_with_rtk(text: str, max_tokens: int, timeout_seconds: int = 10) -> str:
    _ = max_tokens
    path = rtk_binary_path()
    if not path:
        raise RTKError("rtk binary not found")

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [path, "read", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RTKError("rtk read timed out") from exc
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RTKError(stderr or "rtk read failed")

    output = (result.stdout or "").strip()
    if not output:
        raise RTKError("rtk returned empty output")
    return output


def store_original_with_rtk(text: str, timeout_seconds: int = 10) -> str | None:
    """Store `text` in the rtkx CCR store; return its handle (None on failure).

    Best-effort: reversibility is optional, so a missing binary or any error
    yields None rather than raising.
    """
    path = rtk_binary_path()
    if not path:
        return None

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8") as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [path, "ccr", "store", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    if result.returncode != 0:
        return None
    handle = (result.stdout or "").strip()
    return handle or None


def restore_original_with_rtk(handle: str, timeout_seconds: int = 10) -> str:
    """Restore the original content for a CCR `handle` via rtkx."""
    path = rtk_binary_path()
    if not path:
        raise RTKError("rtk binary not found")

    try:
        result = subprocess.run(
            [path, "ccr", "restore", handle],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RTKError("rtk ccr restore timed out") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RTKError(stderr or f"rtk ccr restore failed for {handle}")
    return result.stdout or ""
