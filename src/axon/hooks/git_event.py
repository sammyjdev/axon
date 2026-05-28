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
import uuid
from datetime import UTC, datetime
from pathlib import Path

from axon.code.diff_symbols import symbols_touched_by_commit
from axon.config.runtime import load_runtime_config
from axon.core.decision import Decision
from axon.core.edge import Edge
from axon.hooks.file_bridge import update_context_file
from axon.observability.trace_store import TraceStore
from axon.obsidian.discovery import discover_vault
from axon.obsidian.exporter import export_adr, export_architecture_doc
from axon.store.graph_store import GraphStore
from axon.store.session_store import SessionStore
from axon.triggers.scope_detector import detect_scope_end
from axon.validation.judge import score_decision

logger = logging.getLogger(__name__)

_TRACE_STORE = TraceStore(load_runtime_config())

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


async def _link_touched_symbols(
    store: SessionStore,
    decision_id: str,
    root: Path,
    commit_hash: str,
    graph_store: GraphStore | None,
) -> None:
    """Link a Decision to the symbols its commit touched; refresh Redis cache."""
    try:
        touched = symbols_touched_by_commit(root, commit_hash)
    except Exception as exc:  # symbol linking is best-effort, never fatal
        logger.warning("symbol linking skipped: %s", exc)
        return
    if not touched:
        return
    graph = graph_store or GraphStore()
    try:
        for symbol in touched:
            await store.add_node(
                symbol.id,
                "symbol",
                label=symbol.id,
                payload=symbol.model_dump(mode="json"),
            )
            await store.add_edge(
                Edge(source_id=decision_id, target_id=symbol.id, type="touches")
            )
            await graph.invalidate(symbol.id)
    finally:
        if graph_store is None:
            await graph.close()


async def on_commit(
    *,
    store: SessionStore | None = None,
    cwd: Path | None = None,
    graph_store: GraphStore | None = None,
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
        existing = await store.find_decision_by_git_hash(commit_hash, repo=root.name)
        if existing is not None:
            current_agent = _detect_agent()
            if existing.agent != current_agent:
                refreshed = existing.model_copy(update={"agent": current_agent})
                await store.save_decision(refreshed)
                existing = refreshed
            await _link_touched_symbols(
                store, existing.id, root, commit_hash, graph_store
            )
            try:
                await update_context_file(root, store=store)
            except Exception as exc:
                logger.warning("context.md update skipped: %s", exc)
            logger.info(
                "idempotent skip: decision %s already captured for commit %s",
                existing.id,
                commit_hash[:8],
            )
            return existing.id

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
        await _link_touched_symbols(store, decision.id, root, commit_hash, graph_store)
        try:
            await update_context_file(root, store=store)
        except Exception as exc:  # the .md mirror is a convenience, never fatal
            logger.warning("context.md update skipped: %s", exc)

        # dec-110 / dec-111 / issue #15: also run ADR inference. The
        # orchestrator short-circuits when the commit has no architectural
        # signal, so this is cheap for the common case. Failures are
        # logged but never block git.
        try:
            from axon.adr.inference import InferenceStatus, run_for_head_async

            result = await run_for_head_async(project=root.name, repo_root=root)
            if result.status is InferenceStatus.SAVED_ADR:
                logger.info("inferred ADR: %s", result.title)
            elif result.status is InferenceStatus.GATE_FAILED:
                layer = result.outcome.failed_layer if result.outcome else None
                logger.info(
                    "ADR demoted to draft (%s): %s", layer, result.title
                )
        except Exception as exc:  # best-effort; never block git
            logger.warning("ADR inference skipped: %s", exc)

        logger.info("captured draft decision %s from commit %s", decision.id, commit_hash[:8])
        return decision.id
    finally:
        if owns_store:
            await store.close()


async def _judge_and_export(
    store: SessionStore, root: Path, decisions: list[Decision]
) -> None:
    """Score unjudged draft decisions and export the repo's docs to the vault."""
    trace_id = uuid.uuid4().hex
    recorder = _TRACE_STORE.recorder(trace_id=trace_id, caller="capture")
    scored: list[Decision] = []
    threshold = 3.5
    for decision in decisions:
        if decision.status == "draft" and not decision.judged:
            score = await score_decision(decision)
            if score is not None:
                # model_validate re-runs Pydantic field validators
                # (model_copy bypasses them) so an out-of-range score never
                # poisons the persisted row.
                decision = Decision.model_validate(
                    {
                        **decision.model_dump(mode="python"),
                        "validation_score": float(score),
                        "judged": True,
                    }
                )
                await store.save_decision(decision)
                recorder.append_stage(
                    "validation_result",
                    payload={
                        "decision_id": decision.id,
                        "score": float(score),
                        "threshold": threshold,
                        "passed": score >= threshold,
                    },
                )
        scored.append(decision)

    vault = discover_vault()
    if vault is None:
        logger.info("push: scope ended but no vault discovered — export skipped")
        return
    for decision in scored:
        export_adr(decision, vault=vault)
    export_architecture_doc(scored, vault=vault, name=root.name)
    logger.info("push: exported %d decision(s) to %s", len(scored), vault)


async def on_push(
    *, store: SessionStore | None = None, cwd: Path | None = None
) -> None:
    """On push, if the work scope has closed, judge decisions and export docs."""
    owns_store = store is None
    store = store or _default_store()
    try:
        await store.init()
        root = _repo_root(cwd)
        decisions = await store.find_decisions_by_repo(root.name)
        milestone = os.environ.get("AXON_MILESTONE", "") == "1"
        signal = detect_scope_end(
            root, milestone=milestone, decisions_since_export=len(decisions)
        )
        if signal is None:
            logger.info("push: scope still open, no export")
            return
        logger.info("push: scope ended (%s: %s)", signal.reason, signal.detail)
        await _judge_and_export(store, root, decisions)
    finally:
        if owns_store:
            await store.close()


async def on_init(
    *, store: SessionStore | None = None, cwd: Path | None = None
) -> None:
    """Repo init hook. Code indexing lands in Phase 4."""
    logger.info("init event received (code indexing lands in Phase 4)")


async def on_post_merge_or_checkout(
    *, store: SessionStore | None = None, cwd: Path | None = None
) -> None:
    """Trigger L1-full revalidation of pending ADR drafts (dec-111).

    Re-runs L1-full against every active draft and updates
    ``last_l1_full_at`` so the doctor's ``stale-pending`` check clears.
    Background task — failure is never fatal.
    """
    try:
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from pathlib import Path as _Path

        from axon.adr.draft_pool import list_drafts, write_draft
        from axon.adr.gates.l1 import l1_full

        for record in list_drafts(include_dormant=False):
            passed, _ = l1_full(
                f"{record.title}\n{record.context}\n"
                f"{record.decision}\n{record.rationale}",
                repo_root=_Path.cwd(),
            )
            record.last_l1_full_at = _dt.now(_UTC)
            if not passed:
                record.dormant = True
            write_draft(record)
    except Exception as exc:  # never block git
        logger.warning("on_post_merge_or_checkout failed: %s", exc)


_HANDLERS = {
    "commit": on_commit,
    "push": on_push,
    "init": on_init,
    "post-merge": on_post_merge_or_checkout,
    "post-checkout": on_post_merge_or_checkout,
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
