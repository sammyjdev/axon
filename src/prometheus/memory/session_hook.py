"""End-of-session hook: appends session summary to today's daily note."""
from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


def _daily_note_path(vault_path: Path) -> Path:
    today = date.today().isoformat()
    return vault_path / "daily" / f"{today}.md"


def _ensure_daily_note(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"# {path.stem}\n\n", encoding="utf-8")


async def append_session_summary(summary: str, vault_path: Path | None = None) -> None:
    """Appends the session summary to today's daily note in the vault."""
    if not summary.strip():
        return

    vault = vault_path or Path(os.environ.get("PROMETHEUS_VAULT", Path.home() / "vault"))
    daily = _daily_note_path(vault)
    _ensure_daily_note(daily)

    section = f"\n## Session Summary\n\n{summary.strip()}\n"
    with daily.open("a", encoding="utf-8") as f:
        f.write(section)

    logger.info("Session summary appended to %s", daily)


async def run_end_of_session_hook(summary: str) -> None:
    """Main entry point called at the end of a session."""
    await append_session_summary(summary)
