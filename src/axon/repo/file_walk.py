# src/axon/repo/file_walk.py
from __future__ import annotations

import subprocess
from pathlib import Path

# Local copy - do NOT import from axon.embedder.pipeline (circular import).
# SYNC NOTE: this set must be kept in sync with EXCLUDED_DIR_NAMES in
# axon/embedder/pipeline.py. If you add a directory to one, add it to both.
_EXCLUDED_DIR_NAMES = {
    ".aws-sam",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "dist-packages",
    "site-packages",
    "node_modules",
    "target",
    "venv",
}


def iter_git_files(root: Path, *, suffixes: set[str]) -> list[Path]:
    """List tracked source files respecting .gitignore (D3 security fix).

    Uses 'git ls-files --cached' to list only committed/staged files.
    Applies 'git check-ignore -z --stdin' (null-delimited) to exclude files
    that are gitignored - including files committed before a matching
    .gitignore rule was added. Untracked files require 'git add' first.

    SECURITY GUARANTEE: no gitignored file is returned.

    Fallback: when 'git' is unavailable or root is not a git repo, uses
    rglob with _EXCLUDED_DIR_NAMES filtering. The fallback does NOT guarantee
    exclusion of gitignored files - callers outside git repos must accept
    this limitation.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return _fallback_rglob(root, suffixes)

    all_tracked = [
        root / line
        for line in result.stdout.splitlines()
        if line and Path(line).suffix in suffixes
    ]
    if not all_tracked:
        return []

    # Apply git check-ignore (null-delimited) to filter gitignored paths.
    # --no-index is REQUIRED: every path here comes from 'ls-files --cached'
    # (i.e. is tracked), and without --no-index check-ignore never reports a
    # tracked path - so a file committed before a matching .gitignore rule
    # would silently leak. --no-index evaluates the rules regardless of index
    # state, catching exactly that case.
    # Using -z so paths with spaces are handled correctly. Paths use as_posix()
    # so they match git's forward-slash convention on both sides (Windows
    # str(relative_to) yields backslashes that would never match git's output).
    # Bytes mode + explicit UTF-8 avoids Windows code-page corruption.
    rel_posix = {p: p.relative_to(root).as_posix() for p in all_tracked}
    try:
        check_input_bytes = ("\0".join(rel_posix.values()) + "\0").encode("utf-8")
        ignore_result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--no-index", "--stdin", "-z"],
            input=check_input_bytes,
            capture_output=True,
        )
        # Output is also null-delimited; parse on NUL byte, not splitlines()
        raw = ignore_result.stdout.decode("utf-8")
        ignored_set: set[str] = {
            p for p in raw.split("\0") if p
        }
    except (subprocess.CalledProcessError, FileNotFoundError):
        ignored_set = set()

    return [
        p for p in all_tracked
        if rel_posix[p] not in ignored_set and p.is_file()
    ]


def _fallback_rglob(root: Path, suffixes: set[str]) -> list[Path]:
    """Rglob fallback for non-git directories. Does not exclude gitignored files."""
    result: list[Path] = []
    for path in root.rglob("*"):
        if any(part in _EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        if path.is_file() and path.suffix in suffixes:
            result.append(path)
    return result
