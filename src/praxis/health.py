"""Server health reporting."""

from __future__ import annotations

import time
from typing import Any

from praxis import __version__

_START_MONOTONIC = time.monotonic()


def uptime_seconds() -> float:
    """Seconds elapsed since this process imported Praxis."""
    return round(time.monotonic() - _START_MONOTONIC, 3)


def health_report() -> dict[str, Any]:
    """Return the health payload: status, version, and uptime."""
    return {
        "status": "ok",
        "version": __version__,
        "uptime": uptime_seconds(),
    }
