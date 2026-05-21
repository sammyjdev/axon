"""End-of-session hook: appends session summary to today's daily note."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from pathlib import Path

from axon.config.runtime import load_runtime_config

logger = logging.getLogger(__name__)
_RUNTIME = load_runtime_config()


def _daily_note_path(vault_path: Path) -> Path:
    today = date.today().isoformat()
    return vault_path / "daily" / f"{today}.md"


def _ensure_daily_note(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"# {path.stem}\n\n", encoding="utf-8")


def _append_to_daily(path: Path, section: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(section)


async def append_session_summary(summary: str, vault_path: Path | None = None) -> None:
    """Appends the session summary to today's daily note in the vault."""
    if not summary.strip():
        return

    vault = vault_path or _RUNTIME.vault_root
    daily = _daily_note_path(vault)
    await asyncio.to_thread(_ensure_daily_note, daily)

    section = f"\n## Session Summary\n\n{summary.strip()}\n"
    await asyncio.to_thread(_append_to_daily, daily, section)

    logger.info("Session summary appended to %s", daily)


async def run_end_of_session_hook(summary: str) -> None:
    """Main entry point called at the end of a session."""
    await append_session_summary(summary)
