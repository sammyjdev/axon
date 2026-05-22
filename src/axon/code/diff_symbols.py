"""Symbols touched by a commit's diff (T4.3).

Parses ``git show --unified=0`` hunk headers to learn which lines a commit
changed, then chunks each changed source file and reports the symbols whose
line span overlaps a changed line.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from axon.code.indexer import _INDEXED_LANGUAGES, _symbols_for_file
from axon.core.symbol import Symbol

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _changed_lines_by_file(diff: str) -> dict[str, set[int]]:
    """Map each changed file (new-side path) to the set of changed line numbers."""
    changed: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current = None
            else:
                current = target[2:] if target.startswith("b/") else target
        elif line.startswith("@@") and current is not None:
            match = _HUNK_RE.match(line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2)) if match.group(2) else 1
                if count > 0:
                    changed.setdefault(current, set()).update(
                        range(start, start + count)
                    )
    return changed


def symbols_touched_by_commit(
    repo_root: Path | str, commit_hash: str
) -> list[Symbol]:
    """Symbols whose span overlaps a line changed by ``commit_hash``.

    Reads the post-commit working tree from disk; returns ``[]`` on any git
    failure (a hook must never block).
    """
    root = Path(repo_root)
    try:
        diff = subprocess.check_output(
            ["git", "-C", str(root), "show", commit_hash,
             "--unified=0", "--no-color", "--format="],
            text=True,
        )
    except subprocess.CalledProcessError:
        return []

    touched: dict[str, Symbol] = {}
    for rel_path, lines in _changed_lines_by_file(diff).items():
        path = root / rel_path
        if path.suffix not in _INDEXED_LANGUAGES or not path.is_file():
            continue
        for symbol in _symbols_for_file(path):
            if symbol.id in touched:
                continue
            if any(symbol.start_line <= ln <= symbol.end_line for ln in lines):
                touched[symbol.id] = symbol
    return list(touched.values())
