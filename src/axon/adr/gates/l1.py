"""L1 structural validation (dec-111).

Two tiers:

- **l1_light**: fast (SLA <100ms) — checks via ``git`` only. Files
  cited by the ADR must exist in the post-commit tree, and any
  identifier-like tokens (CamelCase, snake_case, dotted paths) must
  appear in the working tree via ``git grep``. Runs in the hook path.
- **l1_full**: slow, runs in background — validates against the
  tree-sitter symbol graph. Currently a thin wrapper that returns
  ``(True, "stub")`` and is filled in by Fase 2d (issue #10
  triggers task).

Both gates are conservative: they look for *anchors*, not exhaustive
correctness. The goal is to fail commits whose ADR cites symbols that
demonstrably do not exist.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Heuristic patterns to extract candidate file paths and identifiers
# from ADR text.
_PATH_RE = re.compile(r"\b[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,6}\b")
_IDENT_RE = re.compile(r"\b(?:[A-Z][a-z0-9]+){2,}\b|\b[a-z_][a-z0-9_]{4,}\b")


def extract_candidates(adr_text: str) -> tuple[list[str], list[str]]:
    """Return ``(paths, identifiers)`` heuristically extracted from text.

    Paths look like ``src/foo.py``; identifiers look like CamelCase
    classes or snake_case symbols. Both lists are deduplicated while
    preserving order. Identifiers shorter than 5 chars are dropped to
    cut noise — they tend to be common words, not symbols.
    """
    paths: list[str] = []
    seen_paths: set[str] = set()
    for m in _PATH_RE.findall(adr_text):
        if m not in seen_paths and "/" in m:
            seen_paths.add(m)
            paths.append(m)

    idents: list[str] = []
    seen_idents: set[str] = set()
    for m in _IDENT_RE.findall(adr_text):
        if m not in seen_idents and len(m) >= 5:
            seen_idents.add(m)
            idents.append(m)

    return paths, idents


def l1_light(
    adr_text: str,
    *,
    repo_root: Path,
    git_runner: callable | None = None,
) -> tuple[bool, dict[str, object]]:
    """Fast L1 check via git. Returns ``(passed, details)``.

    Passes if every cited path exists in HEAD AND every cited
    identifier has at least one grep match in the tracked tree. Empty
    candidate lists short-circuit to pass (nothing to disprove).
    """
    paths, idents = extract_candidates(adr_text)
    run = git_runner or _git

    missing_paths: list[str] = []
    for path in paths:
        try:
            run(repo_root, "cat-file", "-e", f"HEAD:{path}")
        except subprocess.CalledProcessError:
            missing_paths.append(path)

    missing_idents: list[str] = []
    for ident in idents:
        try:
            out = run(repo_root, "grep", "-l", "--fixed-strings", ident, "HEAD")
            if not out.strip():
                missing_idents.append(ident)
        except subprocess.CalledProcessError:
            missing_idents.append(ident)

    if missing_paths or missing_idents:
        return False, {
            "missing_paths": missing_paths,
            "missing_idents": missing_idents,
        }
    return True, {"paths_checked": paths, "idents_checked": idents}


def l1_full(adr_text: str, *, repo_root: Path) -> tuple[bool, dict[str, object]]:
    """Slow L1 check via tree-sitter graph. Stub — populated by Fase 2d.

    Returns ``(True, {"stub": True})`` to keep the orchestrator wiring
    valid without blocking draft promotion. The Fase 2d trigger task
    plugs in the real symbol-graph validation.
    """
    return True, {"stub": True}


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        text=True,
        cwd=str(root),
        stderr=subprocess.DEVNULL,
    )
