"""Stable git repository identity for linked worktrees (dec-129)."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

#: git says this, on stderr, when the path simply is not a repository. That is
#: the one failure where falling back to the directory name is right and
#: uninteresting - anything else (safe.directory refusal, unreadable .git, git
#: missing from PATH, a locked or corrupt repo) means we ARE in a repository
#: and silently returning the basename reintroduces exactly the bug dec-129
#: fixed: a worktree filing its decisions under a repo that does not exist.
_NOT_A_REPO = "not a git repository"


def repo_identity(path: Path | str | None = None) -> str:
    """Return the parent repository's bare name, falling back to the basename."""
    root = Path(path) if path is not None else Path.cwd()
    try:
        common = subprocess.check_output(  # noqa: S603
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],  # noqa: S603, S607
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as exc:
        if _NOT_A_REPO not in (exc.stderr or "").lower():
            logger.warning(
                "git could not resolve a repo identity for %s (%s); falling back to "
                "the directory name, which is unstable across worktrees: %s",
                root,
                exc.returncode,
                (exc.stderr or "").strip()[:200],
            )
        return root.name
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "could not run git to resolve a repo identity for %s (%s); falling back "
            "to the directory name, which is unstable across worktrees",
            root,
            exc,
        )
        return root.name
    git_dir = (root / common).resolve()
    if git_dir.name == ".git":
        return git_dir.parent.name or root.name
    return git_dir.name.removesuffix(".git") or root.name
