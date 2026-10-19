"""The steps `axon session-hook` runs when a session ends, in order.

Each step is guarded: one that fails is reported on stderr and the next still
runs, and nothing reaches the agent. Phases C and D each add one step here.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path


def store_digest(transcript_path: str, cwd: str) -> None:
    from axon.cli.pb import session_save

    session_save(cwd=cwd, transcript=transcript_path)


def import_transcript_step(transcript_path: str, cwd: str) -> None:
    """Database down: the cursor stays put and the next import (the next
    session end, or a backfill) reads the same lines again - nothing is lost."""
    from axon.activity.importer import import_transcript
    from axon.activity.repository import PostgresActivityRepository
    from axon.config.runtime import load_runtime_config

    async def _run() -> None:
        repo = PostgresActivityRepository(load_runtime_config().pg_url)
        try:
            await repo.ensure_schema()
            await import_transcript(Path(transcript_path), repo)
        finally:
            await repo.close()

    asyncio.run(_run())


STEPS: list[tuple[str, Callable[[str, str], None]]] = [
    ("digest", store_digest),
    ("import transcript", import_transcript_step),
]


def run_session_end(transcript_path: str, cwd: str) -> None:
    from axon.activity import importer

    # Off-limits tree: no step may read the transcript or key anything by it.
    try:
        if importer.transcript_is_restricted(Path(transcript_path), cwd):
            return
    except Exception as exc:  # a hook must never interrupt the agent
        print(f"[axon] session-hook: unreadable transcript: {exc}", file=sys.stderr)
        return
    for name, step in STEPS:
        try:
            step(transcript_path, cwd)
        except Exception as exc:  # a hook must never interrupt the agent
            print(f"[axon] session-hook: {name} failed: {exc}", file=sys.stderr)
