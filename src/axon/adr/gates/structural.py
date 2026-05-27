"""Structural detector for dec-111.

Flags commits whose diff is dominated by file/dir moves rather than
content changes. When ``True``, downstream density gates relax: such
commits have rationale text that necessarily mirrors the diff (rename
descriptions), so requiring a lexicon hit "outside the diff" would
produce false-negatives.

Heuristics (any one triggers structural mode):

- ≥ 2 renames detected by ``git diff --find-renames=80%``
- ≥ 3 new files in directories that did not exist before
- ≥ 2 directory moves (any rename whose old and new paths differ in
  at least the first segment)
- Diff is > 90% path-only changes (rename hunks dominate over textual
  edits)
"""

from __future__ import annotations

from pathlib import PurePosixPath

from axon.adr.commit_context import CommitContext


def is_structural(ctx: CommitContext) -> bool:
    """Return True if the commit looks like a structural refactor."""
    if len(ctx.renames) >= 2:
        return True

    if _new_files_in_new_dirs(ctx) >= 3:
        return True

    if _dir_moves(ctx) >= 2:
        return True

    if _path_only_ratio(ctx) > 0.9:
        return True

    return False


def _new_files_in_new_dirs(ctx: CommitContext) -> int:
    """Count new files whose parent directory contains no pre-existing file.

    Approximation: a directory is "new" if no other ``files_changed`` or
    ``deleted_files`` entry shares its parent path. This is conservative
    — we cannot consult the filesystem at commit time without an extra
    git call, and the goal is just to detect "many new files clustered
    in fresh directories".
    """
    existing_parents: set[str] = set()
    for path in ctx.files_changed:
        if path in ctx.new_files:
            continue
        existing_parents.add(str(PurePosixPath(path).parent))
    for path in ctx.deleted_files:
        existing_parents.add(str(PurePosixPath(path).parent))

    count = 0
    for path in ctx.new_files:
        parent = str(PurePosixPath(path).parent)
        if parent not in existing_parents:
            count += 1
    return count


def _dir_moves(ctx: CommitContext) -> int:
    """Count renames whose first path segment changes."""
    count = 0
    for old, new in ctx.renames:
        old_first = PurePosixPath(old).parts[:1]
        new_first = PurePosixPath(new).parts[:1]
        if old_first != new_first:
            count += 1
    return count


def _path_only_ratio(ctx: CommitContext) -> float:
    """Fraction of diff hunks that are pure rename markers vs content edits.

    Counts lines in the diff that match ``rename from`` / ``rename to``
    or are pure ``+++`` / ``---`` / ``diff --git`` headers, vs lines
    starting with ``+`` or ``-`` (content). A high ratio means the
    diff is dominated by structural change.
    """
    if not ctx.diff:
        return 0.0
    lines = ctx.diff.splitlines()
    structural = 0
    content = 0
    for line in lines:
        if (
            line.startswith("rename from ")
            or line.startswith("rename to ")
            or line.startswith("similarity index ")
            or line.startswith("diff --git ")
        ):
            structural += 1
        elif line.startswith("+") or line.startswith("-"):
            # ``+++`` / ``---`` headers are not content
            if line.startswith(("+++", "---")):
                structural += 1
            else:
                content += 1
    total = structural + content
    if total == 0:
        return 0.0
    return structural / total
