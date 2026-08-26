"""Stable git repository identity for linked worktrees (dec-129)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def repo_identity(path: Path | str | None = None) -> str:
    """Return the parent repository's bare name, falling back to the basename."""
    root = Path(path) if path is not None else Path.cwd()
    try:
        common = subprocess.check_output(  # noqa: S603
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],  # noqa: S603, S607
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return root.name
    git_dir = (root / common).resolve()
    if git_dir.name == ".git":
        return git_dir.parent.name or root.name
    return git_dir.name.removesuffix(".git") or root.name
