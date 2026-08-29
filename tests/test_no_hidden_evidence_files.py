"""Evidence behind a published number must not be hidden from git.

`data/compression/stats.jsonl` carried the skip-worktree bit for two months.
`git status` and `git diff` reported the tree clean while it grew from 209 to
862 lines, and the compression figures in docs/METRICS.md went stale with no
signal at all. Only `git ls-files -v` revealed the `S`.

skip-worktree is a local index bit, so this cannot fail on a fresh clone - it
fails on the machine that set it, which is exactly where the damage happens.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Files that are committed specifically as evidence for a published claim.
EVIDENCE_FILES = ("data/compression/stats.jsonl",)


def test_no_evidence_file_is_hidden_by_skip_worktree() -> None:
    out = subprocess.run(  # noqa: S603
        ["git", "ls-files", "-v", *EVIDENCE_FILES],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    ).stdout

    hidden = [
        line for line in out.splitlines() if line[:1] in {"S", "s"}
    ]

    assert not hidden, (
        "skip-worktree is set on an evidence file, so git will report the tree "
        f"clean while it changes: {hidden}. Run `git update-index "
        "--no-skip-worktree <path>`."
    )
