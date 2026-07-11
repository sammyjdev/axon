"""Scope-end detection — decides when a work scope has closed (T5.2).

A closed scope is the trigger for running the LLM-judge and exporting docs.
Three signals, checked in priority order:

1. ``milestone`` — an explicit marker (``git push --milestone`` / ``axon
   mark-done``).
2. ``git-tag`` — a tag points at HEAD.
3. ``decision-threshold`` — enough decisions piled up since the last export.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DECISION_THRESHOLD = 10


@dataclass(frozen=True)
class ScopeSignal:
    """A reason a scope is considered closed."""

    reason: str  # "milestone" | "git-tag" | "decision-threshold"
    detail: str


def tag_at_head(repo_root: Path | str) -> str | None:
    """Return a tag pointing at HEAD, or None if there is none."""
    try:
        out = subprocess.check_output(  # noqa: S603
            ["git", "-C", str(repo_root), "tag", "--points-at", "HEAD"],  # noqa: S607
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    tags = [tag for tag in out.splitlines() if tag.strip()]
    return tags[0] if tags else None


def detect_scope_end(
    repo_root: Path | str | None = None,
    *,
    milestone: bool = False,
    decisions_since_export: int = 0,
    threshold: int = DEFAULT_DECISION_THRESHOLD,
) -> ScopeSignal | None:
    """Return the scope-end signal, or None while the scope is still open."""
    if milestone:
        return ScopeSignal("milestone", "explicit milestone marker")
    if repo_root is not None:
        tag = tag_at_head(repo_root)
        if tag is not None:
            return ScopeSignal("git-tag", tag)
    if decisions_since_export >= threshold:
        return ScopeSignal(
            "decision-threshold",
            f"{decisions_since_export} decisions since last export",
        )
    return None
