"""AXON git event handlers.

Invoked by the installed git hooks as ``python -m axon.hooks.git_event <event>``
where ``<event>`` is ``commit`` or ``post-merge``. Every handler fails silently
— a hook must never block a git operation. An unknown event, including the
``push`` and ``post-checkout`` that hooks installed by older versions still
send, does nothing.

Only a commit that carries a signal is captured, and no handler calls an LLM:
``arch:`` / ``decision:`` subjects and the ``ADR-Decision:`` trailer become a
draft Decision, a ``Lesson:`` trailer becomes a lesson draft. A plain commit
touches nothing (dec-138).
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from axon.adr.signal import detect, detect_lesson
from axon.core.decision import SUMMARY_MAX_LEN, Decision
from axon.core.repo_identity import repo_identity
from axon.hooks.file_bridge import update_context_file
from axon.store.session_store import SessionStore

logger = logging.getLogger(__name__)

_AGENTS = {"claude-code", "codex", "cursor", "manual"}


def _git(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()  # noqa: S603, S607


def _repo_root(cwd: Path | None = None) -> Path:
    return Path(_git(["rev-parse", "--show-toplevel"], cwd))


def _detect_agent() -> str:
    agent = os.environ.get("AXON_AGENT", "manual")
    return agent if agent in _AGENTS else "manual"


def _default_store() -> SessionStore:
    from axon.config.runtime import load_runtime_config

    return SessionStore(db_path=load_runtime_config().data_root / "axon.db")


async def on_commit(
    *,
    store: SessionStore | None = None,
    cwd: Path | None = None,
) -> str | None:
    """Capture the most recent commit if it carries a signal.

    Returns the decision id, or None when the commit carries no decision signal.
    """
    root = _repo_root(cwd)
    message = _git(["log", "-1", "--pretty=%B"], root)
    signal = detect(message)
    lesson = detect_lesson(message)
    if signal is None and lesson is None:
        return None

    commit_hash = _git(["log", "-1", "--pretty=%H"], root)
    files = _git(["log", "-1", "--name-only", "--format=", "HEAD"], root).splitlines()
    if lesson is not None:
        _write_lesson_draft(commit_hash, lesson.title, files)
    if signal is None:
        return None

    owns_store = store is None
    store = store or _default_store()
    try:
        await store.init()
        repo = repo_identity(root)
        existing = await store.find_decision_by_git_hash(commit_hash, repo=repo)
        if existing is not None:
            current_agent = _detect_agent()
            if existing.agent != current_agent:
                existing = existing.model_copy(update={"agent": current_agent})
                await store.save_decision(existing)
            decision_id = existing.id
            logger.info(
                "idempotent skip: decision %s already captured for commit %s",
                existing.id,
                commit_hash[:8],
            )
        else:
            decision = Decision(
                id=await store.next_decision_id(),
                timestamp=datetime.now(UTC),
                agent=_detect_agent(),
                repo=repo,
                files=[Path(f) for f in files if f],
                summary=_git(["log", "-1", "--pretty=%s"], root)[:SUMMARY_MAX_LEN],
                git_hash=commit_hash,
                status="draft",
            )
            decision = await store.add_decision(decision)
            decision_id = decision.id
            logger.info("captured draft decision %s from commit %s", decision_id, commit_hash[:8])
        try:
            await update_context_file(root, store=store)
        except Exception as exc:  # the .md mirror is a convenience, never fatal
            logger.warning("context.md update skipped: %s", exc)
        return decision_id
    finally:
        if owns_store:
            await store.close()


def _write_lesson_draft(commit_hash: str, title: str, files: list[str]) -> None:
    """Queue a lesson draft for a human to flesh out; never overwrite an edited one."""
    try:
        from axon.adr.lesson_pool import LessonDraftRecord, _draft_dir, write_draft

        if (_draft_dir() / f"{commit_hash}.md").exists():
            return
        write_draft(
            LessonDraftRecord(commit_hash=commit_hash, title=title, context="\n".join(files))
        )
        logger.info("captured lesson draft: %s", title)
    except Exception as exc:  # best-effort; never block git
        logger.warning("lesson capture skipped: %s", exc)


_PULL_SCAN_CAP = 50  # ponytail: bounds pathological pulls; raise if a legit sync exceeds it


async def _scan_pulled_range(
    *, store: SessionStore | None = None, cwd: Path | None = None
) -> None:
    """Capture dec-110 signal commits from the just-pulled range (cloud-arm bridge).

    Only commits carrying an explicit architectural signal are captured -
    a pull may contain dozens of mechanical commits and capturing them all
    would be noise. Local commits keep the richer on_commit path.
    """
    root = _repo_root(cwd)
    repo = repo_identity(root)
    try:
        hashes = _git(["rev-list", "--reverse", "ORIG_HEAD..HEAD"], root).splitlines()
    except Exception:
        return  # no ORIG_HEAD (fresh clone / first merge) - nothing pulled
    # Keep the NEWEST cap-window (chronological order preserved): on a
    # pathological pull the recent signal commits matter more than the tail.
    hashes = [h for h in hashes if h][-_PULL_SCAN_CAP:]
    if not hashes:
        return

    owns_store = store is None
    store = store or _default_store()
    try:
        await store.init()
        for commit_hash in hashes:
            message = _git(["log", "-1", commit_hash, "--pretty=%B"], root)
            if detect(message) is None:
                continue
            existing = await store.find_decision_by_git_hash(
                commit_hash, repo=repo
            )
            if existing is not None:
                continue
            subject = _git(["log", "-1", commit_hash, "--pretty=%s"], root)
            files = _git(
                ["log", "-1", commit_hash, "--name-only", "--format="], root
            ).splitlines()
            decision = Decision(
                id=await store.next_decision_id(),
                timestamp=datetime.now(UTC),
                agent="remote",
                repo=repo,
                files=[Path(f) for f in files if f],
                summary=subject[:SUMMARY_MAX_LEN],
                git_hash=commit_hash,
                status="draft",
            )
            await store.add_decision(decision)
    finally:
        if owns_store:
            await store.close()


async def on_post_merge(
    *, store: SessionStore | None = None, cwd: Path | None = None
) -> None:
    """post-merge: capture the signal commits a pull brought in."""
    try:
        await _scan_pulled_range(store=store, cwd=cwd)
    except Exception as exc:  # never block git
        logger.warning("pulled-range scan failed: %s", exc)


_HANDLERS = {
    "commit": on_commit,
    "post-merge": on_post_merge,
}


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
