"""Bring one registered root to its HEAD.

Git is the event log: a sync walks the root with the same indexer every other
writer uses, lets the sha1 cache skip what did not change, and records the
commit it walked. A missed trigger costs nothing, the next sync converges.

A sync always walks, even when HEAD equals the recorded commit: the walk reads
the working tree, which can differ from HEAD, and a no-change walk is cheap
(0.13 s for 843 files, measured 2026-10-10).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from axon.core.file_identity import RepoRoots, identity_for_path
from axon.embedder.engine import EmbedderEngine
from axon.embedder.pipeline import (
    index_path,
    infer_ctx_from_path,
    is_ctx_indexable,
    iter_supported_files,
)
from axon.store.pg_file_cache import PostgresFileCache
from axon.store.pg_symbol_deps import PostgresSymbolDeps
from axon.store.pg_vector_store import PgVectorStore


class SyncCtxError(ValueError):
    """The ctx of a repo could not be decided without the caller."""


@dataclass(frozen=True)
class SyncResult:
    repo: str
    commit: str | None
    files: int = 0
    chunks: int = 0


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


def resolve_root(path: Path, roots: RepoRoots) -> tuple[str, Path] | None:
    """The registered (repo, root) a path belongs to, or None.

    A linked worktree resolves to its main checkout: the index follows the
    registered root, never the worktree's own tree.
    """
    here = Path(path).resolve()
    if here.is_file():
        here = here.parent
    candidate = here
    try:
        common = subprocess.run(  # noqa: S603
            ["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        common = ""
    if common and Path(common).name == ".git":
        candidate = Path(common).resolve().parent
    for repo, root in roots.items():
        if root.resolve() == candidate:
            return repo, root
    # Not a checkout root: the vault, or a plain directory below a registered root.
    identity = identity_for_path(candidate / "_", roots)
    return None if identity is None else (identity[0], roots[identity[0]])


def _vault_languages(per_file_ctx: bool) -> set[str] | None:
    # The vault is indexed as notes only, as `index-vault` always did.
    return {"markdown"} if per_file_ctx else None


def tree_size(root: Path, *, vault_root: Path, per_file_ctx: bool = False) -> tuple[int, int]:
    """Files and characters a first sync of the root would embed. Embeds nothing."""
    files = chars = 0
    for path in iter_supported_files(root, languages=_vault_languages(per_file_ctx)):
        if per_file_ctx and not is_ctx_indexable(infer_ctx_from_path(path, vault_root), None):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            continue  # gone since the listing, as index_path treats it
        files += 1
        chars += len(text)
    return files, chars


async def _repo_ctx(
    repo: str, explicit: str | None, stored: str | None, store: PgVectorStore
) -> str:
    if explicit:
        if stored and explicit != stored:
            # A new ctx would index every file again beside the old copy, which no
            # reconcile reaches: it only visits the ctx it walks.
            raise SyncCtxError(
                f"{repo}: indexed under ctx {stored}; moving it to {explicit} is not supported"
            )
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
    repo_ctx = (
        ""
        if per_file_ctx
        else await _repo_ctx(repo, ctx, state[0] if state else None, store)
    )

    files = chunks = 0
    # Walk again while HEAD moved during the walk (a rebase commits many times).
    # ponytail: no cap; a HEAD that moved during every walk would keep it walking.
    while True:
        walked = _head(root)
        indexed, written = await index_path(
            root,
            engine=engine,
            store=store,
            vault_root=vault_root,
            file_cache=file_cache,
            forced_ctx=repo_ctx or None,
            graph_store=graph_store,
            languages=_vault_languages(per_file_ctx),
            repo_roots=repo_roots,
        )
        files += indexed
        chunks += written
        if _head(root) == walked:
            break

    await file_cache.set_repo_state(repo, repo_ctx, walked)
    return SyncResult(repo=repo, commit=walked, files=files, chunks=chunks)
