"""CommitContext: parsed git state used by the gate pipeline (dec-111).

Carries everything the gates need from a single commit: subject, body,
diff text, files changed, renames, new files, and the repo root. Built
once and shared across gates to avoid redundant ``git`` calls.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CommitContext:
    commit_hash: str
    subject: str
    body: str
    diff: str
    files_changed: list[str] = field(default_factory=list)
    new_files: list[str] = field(default_factory=list)
    renames: list[tuple[str, str]] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    repo_root: Path = field(default_factory=Path.cwd)

    @property
    def message(self) -> str:
        """Full commit message (subject + body, separator handled)."""
        if self.body:
            return f"{self.subject}\n\n{self.body}"
        return self.subject


def from_commit(repo_root: Path | None = None, commit: str = "HEAD") -> CommitContext:
    """Build a ``CommitContext`` from ``commit`` (default ``HEAD``).

    Calls ``git`` once per piece of information. Returns an empty-ish
    context (with ``commit_hash=""``) when the directory is not a repo
    or has no commits yet.
    """
    root = repo_root or Path.cwd()
    # ponytail: omit the ref token for the "HEAD" default so subprocess argv
    # stays byte-identical to before this function took a commit argument
    # (git already defaults "log -1" to HEAD; only non-default commits need
    # an explicit ref).
    ref: list[str] = [] if commit == "HEAD" else [commit]
    try:
        commit_hash = _git(root, "log", "-1", *ref, "--pretty=%H").strip()
    except subprocess.CalledProcessError:
        return CommitContext(commit_hash="", subject="", body="", diff="")

    subject = _git(root, "log", "-1", *ref, "--pretty=%s").rstrip("\n")
    body = _git(root, "log", "-1", *ref, "--pretty=%b").rstrip("\n")
    diff = _git(
        root,
        "diff",
        f"{commit}~1",
        commit,
        "--",
        ":(exclude)*.lock",
        ":(exclude)*.json",
    )
    name_status = _git(
        root, "diff", f"{commit}~1", commit, "--find-renames=80%", "--name-status"
    )

    files_changed: list[str] = []
    new_files: list[str] = []
    renames: list[tuple[str, str]] = []
    deleted_files: list[str] = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            old, new = parts[1], parts[2]
            renames.append((old, new))
            files_changed.append(new)
        elif status == "A" and len(parts) >= 2:
            new_files.append(parts[1])
            files_changed.append(parts[1])
        elif status == "D" and len(parts) >= 2:
            deleted_files.append(parts[1])
        elif len(parts) >= 2:
            files_changed.append(parts[1])

    return CommitContext(
        commit_hash=commit_hash,
        subject=subject,
        body=body,
        diff=diff,
        files_changed=files_changed,
        new_files=new_files,
        renames=renames,
        deleted_files=deleted_files,
        repo_root=root,
    )


def from_head(repo_root: Path | None = None) -> CommitContext:
    """Build a ``CommitContext`` from the current ``HEAD`` commit."""
    return from_commit(repo_root, "HEAD")


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, cwd=str(root))  # noqa: S603, S607
