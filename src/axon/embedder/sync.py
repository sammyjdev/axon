"""Bring one registered root to its HEAD.

Git is the event log: a sync walks the root with the same indexer every other
writer uses, lets the sha1 cache skip what did not change, and records the
commit it walked. A missed trigger costs nothing, the next sync converges.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from axon.core.file_identity import RepoRoots
from axon.embedder.engine import EmbedderEngine
from axon.embedder.pipeline import index_path
from axon.store.pg_file_cache import PostgresFileCache
from axon.store.pg_symbol_deps import PostgresSymbolDeps
from axon.store.pg_vector_store import PgVectorStore

# One extra walk when HEAD moved during the first, so two commits in a row do not
# leave the index one behind. A HEAD that keeps moving is left to the next sync.
_MAX_WALKS = 2


class SyncCtxError(ValueError):
    """The ctx of a repo could not be decided without the caller."""


@dataclass(frozen=True)
class SyncResult:
    repo: str
    commit: str | None
    files: int = 0
    chunks: int = 0
    skipped: bool = False


def _head(root: Path) -> str | None:
    """HEAD of the root, or None outside a git repository (the vault)."""
    try:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


async def _repo_ctx(
    repo: str, explicit: str | None, stored: str | None, store: PgVectorStore
) -> str:
    if explicit:
        return explicit
    if stored:
        ctx = stored
    else:
        legacy = await store.legacy_ctxs(repo)
        if len(legacy) != 1:
            found = ", ".join(legacy) or "none"
            raise SyncCtxError(
                f"{repo}: no single ctx to inherit (legacy rows: {found}); pass --ctx"
            )
        ctx = legacy[0]
    if ctx == "work":
        raise SyncCtxError(f"{repo}: ctx work is never chosen implicitly; pass --ctx work")
    return ctx


async def sync_root(
    root: Path,
    repo: str,
    *,
    engine: EmbedderEngine,
    store: PgVectorStore,
    file_cache: PostgresFileCache,
    vault_root: Path,
    repo_roots: RepoRoots,
    ctx: str | None = None,
    per_file_ctx: bool = False,
    graph_store: PostgresSymbolDeps | None = None,
) -> SyncResult:
    """Index ``root`` under the identity ``repo`` and record the commit walked.

    ``per_file_ctx`` is for the vault, whose ctx comes from each file's top-level
    folder; a repo has one ctx, stored after its first sync.
    """
    state = await file_cache.get_repo_state(repo)
    head = _head(root)
    if state is not None and head is not None and state[1] == head:
        return SyncResult(repo=repo, commit=head, skipped=True)

    repo_ctx = (
        ""
        if per_file_ctx
        else await _repo_ctx(repo, ctx, state[0] if state else None, store)
    )

    files = chunks = 0
    for _ in range(_MAX_WALKS):
        walked = _head(root)
        indexed, written = await index_path(
            root,
            engine=engine,
            store=store,
            vault_root=vault_root,
            file_cache=file_cache,
            forced_ctx=repo_ctx or None,
            graph_store=graph_store,
            repo_roots=repo_roots,
        )
        files += indexed
        chunks += written
        if _head(root) == walked:
            break

    await file_cache.set_repo_state(repo, repo_ctx, walked)
    return SyncResult(repo=repo, commit=walked, files=files, chunks=chunks)
