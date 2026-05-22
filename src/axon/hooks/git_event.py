"""AXON git event handlers.

Invoked by the installed git hooks as ``python -m axon.hooks.git_event <event>``
where ``<event>`` is ``commit``, ``push`` or ``init``. Every handler fails
silently — a hook must never block a git operation.

``on_commit`` is fully implemented here. ``on_push`` (milestone / LLM-judge)
lands in Phase 5; ``on_init`` (code indexing) lands in Phase 4.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from axon.core.decision import Decision
from axon.hooks.file_bridge import update_context_file
from axon.store.session_store import SessionStore

logger = logging.getLogger(__name__)

_AGENTS = {"claude-code", "codex", "cursor", "manual"}


def _git(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def _repo_root(cwd: Path | None = None) -> Path:
    return Path(_git(["rev-parse", "--show-toplevel"], cwd))


def _detect_agent() -> str:
    agent = os.environ.get("AXON_AGENT", "manual")
    return agent if agent in _AGENTS else "manual"


def _default_store() -> SessionStore:
    from axon.config.runtime import load_runtime_config

    return SessionStore(db_path=load_runtime_config().data_root / "axon.db")


async def on_commit(
    *, store: SessionStore | None = None, cwd: Path | None = None
) -> str | None:
    """Capture a draft Decision from the most recent commit.

    Returns the new decision id, or None if there was nothing to capture.
    """
    owns_store = store is None
    store = store or _default_store()
    try:
        await store.init()
        root = _repo_root(cwd)
        commit_hash = _git(["log", "-1", "--pretty=%H"], root)
        subject = _git(["log", "-1", "--pretty=%s"], root)
        files = _git(
            ["log", "-1", "--name-only", "--format=", "HEAD"], root
        ).splitlines()
        decision = Decision(
            id=await store.next_decision_id(),
            timestamp=datetime.now(UTC),
            agent=_detect_agent(),
            repo=root.name,
            files=[Path(f) for f in files if f],
            summary=subject[:80],
            git_hash=commit_hash,
            status="draft",
        )
        await store.save_decision(decision)
        try:
            await update_context_file(root, store=store)
        except Exception as exc:  # the .md mirror is a convenience, never fatal
            logger.warning("context.md update skipped: %s", exc)
        logger.info("captured draft decision %s from commit %s", decision.id, commit_hash[:8])
        return decision.id
    finally:
        if owns_store:
            await store.close()


async def on_push(
    *, store: SessionStore | None = None, cwd: Path | None = None
) -> None:
    """Push milestone hook. Scope detection + LLM-judge land in Phase 5."""
    logger.info("push event received (scope detection lands in Phase 5)")


async def on_init(
    *, store: SessionStore | None = None, cwd: Path | None = None
) -> None:
    """Repo init hook. Code indexing lands in Phase 4."""
    logger.info("init event received (code indexing lands in Phase 4)")


_HANDLERS = {"commit": on_commit, "push": on_push, "init": on_init}


def main(argv: list[str] | None = None) -> int:
    """Dispatch a git event. Always returns 0 — a hook must never block git."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in _HANDLERS:
        return 0
    event = argv[0]
    try:
        asyncio.run(_HANDLERS[event]())
    except Exception as exc:  # top-level guard — never block git
        logger.warning("git event %s failed: %s", event, exc)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
