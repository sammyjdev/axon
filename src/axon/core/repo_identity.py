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


def is_git_repo(path: Path | str | None = None) -> bool:
    """True when `path` sits inside a git working tree or worktree.

    The capture hooks refuse a cwd outside any repository instead of keying it
    by basename (closeout F1): `samdev` and `maker-bench` were the home
    directory and a bench scratch root, not projects.
    """
    root = Path(path) if path is not None else Path.cwd()
    target_dir = root.parent if root.is_file() else root
    try:
        subprocess.check_output(  # noqa: S603
            ["git", "-C", str(target_dir), "rev-parse", "--git-common-dir"],  # noqa: S603, S607
            text=True,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def repo_identity(path: Path | str | None = None) -> str:
    """Return the parent repository's bare name, falling back to the basename."""
    root = Path(path) if path is not None else Path.cwd()
    target_dir = root.parent if root.is_file() else root
    try:
        common = subprocess.check_output(  # noqa: S603
            ["git", "-C", str(target_dir), "rev-parse", "--git-common-dir"],  # noqa: S603, S607
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as exc:
        if _NOT_A_REPO not in (exc.stderr or "").lower():
            logger.warning(
                "git could not resolve a repo identity for %s (%s); falling back to "
                "the directory name, which is unstable across worktrees: %s",
                target_dir,
                exc.returncode,
                (exc.stderr or "").strip()[:200],
            )
        return target_dir.name
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "could not run git to resolve a repo identity for %s (%s); falling back "
            "to the directory name, which is unstable across worktrees",
            target_dir,
            exc,
        )
        return target_dir.name
    git_dir = (target_dir / common).resolve()
    if git_dir.name == ".git":
        return git_dir.parent.name or target_dir.name
    return git_dir.name.removesuffix(".git") or target_dir.name


import os  # noqa: E402
from collections.abc import Mapping  # noqa: E402
from dataclasses import dataclass  # noqa: E402

#: Scratch roots under ~/dev that are not repositories (R8).
DEFAULT_SCRATCH_SEGMENTS: frozenset[str] = frozenset({"_bench", "_worktrees", "_wt"})

#: Test-residue directories (R5).
DEFAULT_TMP_SEGMENT_PREFIXES: tuple[str, ...] = ("pytest-of-",)

#: Stable refusal-reason prefixes. Tests assert against these, never a literal,
#: and `cut -f3 refused.tsv | sort | uniq -c` groups on them.
REFUSED_VAULT = "vault root"
REFUSED_SCRATCH = "scratch root"
REFUSED_TEST_RESIDUE = "test residue"
REFUSED_RELATIVE = "relative path, no root"
REFUSED_UNKNOWN = "no known repo segment"


@dataclass(frozen=True, slots=True)
class RepoKeyResolution:
    """The key a path text resolves to, or the reason it is refused."""

    key: str | None
    rule: str  # "R0-bare", "R0-live-git", "R1/R3", "R2", "R7"; "" on refusal
    reason: str | None  # None iff key is not None


def resolve_repo_key(
    path: Path | str,
    *,
    known_names: frozenset[str],
    aliases: Mapping[str, str],
    vault_root: Path,
    scratch_segments: frozenset[str] = DEFAULT_SCRATCH_SEGMENTS,
    tmp_segment_prefixes: tuple[str, ...] = DEFAULT_TMP_SEGMENT_PREFIXES,
) -> RepoKeyResolution:
    """Resolve a repository key from a path text or live directory."""
    path_str = str(path)
    is_home_or_abs = (
        path_str == "~"
        or path_str.startswith("~" + os.sep)
        or path_str.startswith("~/")
        or os.path.isabs(path_str)
    )
    if not is_home_or_abs:
        parts = Path(path).parts
        if len(parts) == 1:
            seg = parts[0]
            if seg in aliases:
                return RepoKeyResolution(key=aliases[seg], rule="R0-bare", reason=None)
            if seg in known_names:
                return RepoKeyResolution(key=seg, rule="R0-bare", reason=None)
        return RepoKeyResolution(key=None, rule="", reason=REFUSED_RELATIVE)

    resolved = Path(os.path.realpath(os.path.expanduser(path_str)))
    target = resolved.parent if resolved.is_file() else resolved

    vr = Path(os.path.realpath(os.path.expanduser(str(vault_root))))
    if target == vr or vr in target.parents:
        return RepoKeyResolution(key=None, rule="", reason=f"{REFUSED_VAULT} {vr}")

    for seg in target.parts:
        if seg in scratch_segments:
            return RepoKeyResolution(key=None, rule="", reason=f"{REFUSED_SCRATCH} {seg}")

    if not target.is_dir():
        for seg in target.parts:
            if any(seg.startswith(prefix) for prefix in tmp_segment_prefixes):
                return RepoKeyResolution(
                    key=None,
                    rule="",
                    reason=f"{REFUSED_TEST_RESIDUE} {seg}",
                )

    if target.is_dir():
        return RepoKeyResolution(key=repo_identity(target), rule="R0-live-git", reason=None)

    for seg in reversed(target.parts):
        if seg in aliases:
            return RepoKeyResolution(key=aliases[seg], rule="R2", reason=None)
        if seg in known_names:
            return RepoKeyResolution(key=seg, rule="R1/R3", reason=None)
        if seg.endswith("-worktrees"):
            stem = seg.removesuffix("-worktrees")
            if stem in known_names:
                return RepoKeyResolution(key=stem, rule="R7", reason=None)

    return RepoKeyResolution(key=None, rule="", reason=REFUSED_UNKNOWN)

