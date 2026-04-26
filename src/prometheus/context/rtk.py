from __future__ import annotations

import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path


class RTKError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def rtk_binary_path() -> str | None:
    return shutil.which("rtk")


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
