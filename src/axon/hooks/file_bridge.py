"""File-based context bridge — maintains ``<repo>/.axon/context.md``.

Agents without MCP (e.g. Cursor) read this file directly. It is refreshed on
git events and on session end. Writes are atomic (temp file + rename).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from axon.core.decision import Decision
from axon.observability.friction import FrictionPattern
from axon.store.session_store import SessionStore

_RECENT_LIMIT = 15


def _render(
    repo: str,
    decisions: list[Decision],
    friction: Sequence[FrictionPattern] = (),
) -> str:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    lines = [
        f"# AXON context — {repo}",
        "",
        f"_Updated {now} · {len(decisions)} recent decision(s)._",
        "",
        "## Recent decisions",
        "",
    ]
    if decisions:
        lines += [f"- `{d.id}` ({d.status}) — {d.summary}" for d in decisions]
    else:
        lines.append("_None captured yet._")

    symbols = sorted({symbol for d in decisions for symbol in d.symbols})
    lines += ["", "## Active symbols", ""]
    lines += [f"- `{symbol}`" for symbol in symbols] if symbols else ["_None._"]
    if friction:
        lines += ["", "## Recurring friction", ""]
        lines += [
            f"- `{pattern.reason_code}` via {pattern.caller} (ctx={pattern.ctx}) - "
            f"{pattern.count}x across {pattern.distinct_days} days"
            for pattern in friction[:5]
        ]
    lines.append("")
    return "\n".join(lines)


async def update_context_file(
    repo_root: Path | str,
    *,
    store: SessionStore,
    friction: Sequence[FrictionPattern] = (),
) -> Path:
    """Write ``<repo_root>/.axon/context.md`` from the repo's recent decisions.

    The write is atomic. Returns the path written.
    """
    root = Path(repo_root)
    decisions = await store.find_decisions_by_repo(root.name, limit=_RECENT_LIMIT)
    content = _render(root.name, decisions, friction)

    axon_dir = root / ".axon"
    axon_dir.mkdir(parents=True, exist_ok=True)
    target = axon_dir / "context.md"
    tmp = axon_dir / "context.md.tmp"
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)
    return target
