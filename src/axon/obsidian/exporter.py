"""Obsidian doc export (T5.3).

Writes Markdown into ``<vault>/AXON/{Architecture,Summaries,Decisions}/``.
Every write is atomic (temp file + ``os.replace``) so a vault is never left
with a half-written note. Docs cross-reference each other via Obsidian
``[[wikilinks]]`` keyed on decision id.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

from axon.core.decision import Decision

_ROOT = "AXON"
_ARCHITECTURE_DIR = "Architecture"
_SUMMARIES_DIR = "Summaries"
_DECISIONS_DIR = "Decisions"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_write(target: Path, content: str) -> Path:
    """Write ``content`` to ``target`` atomically; return the path written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)
    return target


def export_adr(decision: Decision, *, vault: Path) -> Path:
    """Write one decision as an ADR note at ``AXON/Decisions/<id>.md``."""
    files = "\n".join(f"- `{f}`" for f in decision.files) or "_none_"
    symbols = "\n".join(f"- `{s}`" for s in decision.symbols) or "_none_"
    linked = " ".join(f"[[{d}]]" for d in decision.linked_decisions) or "_none_"
    content = "\n".join(
        [
            f"# {decision.id} — {decision.summary}",
            "",
            f"_Exported {_now()}_",
            "",
            f"- **Status:** {decision.status}",
            f"- **Repo:** {decision.repo}",
            f"- **Agent:** {decision.agent}",
            f"- **Timestamp:** {decision.timestamp.isoformat()}",
            f"- **Validation score:** {decision.validation_score}",
            f"- **Git hash:** {decision.git_hash or '—'}",
            f"- **Linked decisions:** {linked}",
            "",
            "## Files",
            "",
            files,
            "",
            "## Symbols",
            "",
            symbols,
            "",
        ]
    )
    return _atomic_write(vault / _ROOT / _DECISIONS_DIR / f"{decision.id}.md", content)


def export_architecture_doc(
    decisions: list[Decision], *, vault: Path, name: str = "architecture"
) -> Path:
    """Write an architecture overview that wikilinks to each decision's note."""
    lines = [
        f"# Architecture — {name}",
        "",
        f"_Exported {_now()} · {len(decisions)} decision(s)._",
        "",
        "## Decisions",
        "",
    ]
    if decisions:
        lines += [
            f"- [[{d.id}]] ({d.status}) — {d.summary}" for d in decisions
        ]
    else:
        lines.append("_None._")
    lines.append("")
    return _atomic_write(
        vault / _ROOT / _ARCHITECTURE_DIR / f"{name}.md", "\n".join(lines)
    )


def export_project_summary(
    repo: str, since: date, decisions: list[Decision], *, vault: Path
) -> Path:
    """Write a summary of a repo's decisions made on or after ``since``."""
    recent = [d for d in decisions if d.timestamp.date() >= since]
    lines = [
        f"# Summary — {repo}",
        "",
        f"_Exported {_now()} · since {since.isoformat()} · "
        f"{len(recent)} decision(s)._",
        "",
        "## Decisions",
        "",
    ]
    if recent:
        lines += [f"- [[{d.id}]] — {d.summary}" for d in recent]
    else:
        lines.append("_None in range._")
    lines.append("")
    return _atomic_write(
        vault / _ROOT / _SUMMARIES_DIR / f"{repo}.md", "\n".join(lines)
    )
