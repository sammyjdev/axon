"""Legacy rows: what the index still holds from before (repo, rel_path).

A legacy row has an absolute ``file_path``. It belongs to the vault when its
path is under the legacy vault root, otherwise to the repo named by its
``project``. ``project`` alone is not enough: vault folders carry repo names.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from axon.core.file_identity import VAULT_REPO, RepoRoots

# A reindexed repo holding under half the files of its old index stops the delete:
# the walker dropped something, and the old rows are the only copy.
STOP_LINE = 0.5

_OLD = """
    SELECT CASE WHEN starts_with(file_path, $1) THEN $2 ELSE project END AS repo,
           count(*) AS chunks, count(DISTINCT file_path) AS files
      FROM embeddings WHERE file_path LIKE '/%' GROUP BY 1
"""
_NEW = """
    SELECT project AS repo, count(*) AS chunks, count(DISTINCT file_path) AS files
      FROM embeddings WHERE file_path NOT LIKE '/%' GROUP BY 1
"""
# The rows of one repo: its own legacy rows minus the vault's, or the vault's.
_MINE = """
    file_path LIKE '/%'
    AND CASE WHEN $2 = $3 THEN starts_with(file_path, $1)
             ELSE project = $2 AND NOT starts_with(file_path, $1) END
"""


@dataclass(frozen=True)
class LegacyRow:
    repo: str
    old_chunks: int
    new_chunks: int
    old_files: int
    new_files: int
    blocked: str  # why the legacy rows cannot be deleted; empty when eligible


def _vault_prefix(legacy_vault_root: str) -> str:
    root = legacy_vault_root.rstrip("/")
    # "" or "/" would make every legacy row the vault's, and the vault's prune a delete
    # of every repo's legacy rows.
    if not root.startswith("/"):
        raise ValueError(f"legacy vault root must be an absolute directory: {legacy_vault_root!r}")
    return root + "/"


def _head(root: Path) -> str | None:
    try:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None


def _blocked(
    repo: str, old_files: int, new_files: int, roots: RepoRoots, state, pending: int
) -> str:
    root = roots.get(repo)
    if root is None or not root.is_dir():
        return "no root on this machine"
    if state is None:
        return "never synced"
    if pending:
        # An index run marks a file pending before it touches its chunks and done
        # after: a pending file is one whose chunks a failed run may have dropped.
        return f"{pending} file(s) left pending by an unfinished index run, run axon sync"
    head = _head(root)
    if head is None:
        return "no git HEAD to check the index against"
    if state["indexed_commit"] != head:
        return "index is behind HEAD, run axon sync"
    if new_files < old_files * STOP_LINE:
        share = round(100 * new_files / old_files)
        return f"new files are {share} percent of old, under the {round(STOP_LINE * 100)} line"
    return ""


async def legacy_report(dsn: str, roots: RepoRoots, legacy_vault_root: str) -> list[LegacyRow]:
    """One row per repo that has legacy rows, plus one for the vault."""
    con = await asyncpg.connect(dsn)
    try:
        old = await con.fetch(_OLD, _vault_prefix(legacy_vault_root), VAULT_REPO)
        new = {r["repo"]: r for r in await con.fetch(_NEW)}
        # The first sync creates repo_state; until then every repo is never synced.
        has_state = await con.fetchval("SELECT to_regclass('repo_state') IS NOT NULL")
        states = (
            {r["repo"]: r for r in await con.fetch("SELECT * FROM repo_state")}
            if has_state
            else {}
        )
        pending = {
            r["repo"]: r["n"]
            for r in await con.fetch(
                "SELECT repo, count(*) AS n FROM file_index"
                " WHERE status <> 'done' AND repo <> '' GROUP BY repo"
            )
        }
    finally:
        await con.close()
    rows = []
    for r in sorted(old, key=lambda r: -r["chunks"]):
        fresh = new.get(r["repo"])
        new_files = fresh["files"] if fresh else 0
        rows.append(
            LegacyRow(
                repo=r["repo"],
                old_chunks=r["chunks"],
                new_chunks=fresh["chunks"] if fresh else 0,
                old_files=r["files"],
                new_files=new_files,
                blocked=_blocked(
                    r["repo"],
                    r["files"],
                    new_files,
                    roots,
                    states.get(r["repo"]),
                    pending.get(r["repo"], 0),
                ),
            )
        )
    return rows


async def prune_legacy(dsn: str, repo: str, legacy_vault_root: str) -> tuple[int, int]:
    """Delete one repo's legacy rows from both tables in one transaction.

    Returns (embeddings rows, file_index rows) deleted. The caller checks
    eligibility; this only ever matches rows with an absolute file_path.
    """
    args = (_vault_prefix(legacy_vault_root), repo, VAULT_REPO)
    con = await asyncpg.connect(dsn)
    try:
        async with con.transaction():
            # file_index has no project column: its legacy rows are found through
            # the chunks they belong to, so they go first.
            cached = await con.execute(
                "DELETE FROM file_index WHERE repo = '' AND (file_path, ctx) IN"  # noqa: S608
                f" (SELECT DISTINCT file_path, ctx FROM embeddings WHERE {_MINE})",
                *args,
            )
            chunks = await con.execute(f"DELETE FROM embeddings WHERE {_MINE}", *args)  # noqa: S608
    finally:
        await con.close()
    return int(chunks.split()[-1]), int(cached.split()[-1])
