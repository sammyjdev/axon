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

import yaml  # type: ignore[import-untyped]

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


def _render_note(frontmatter: dict[str, object], body: str) -> str:
    """Render a vault note: YAML frontmatter block + markdown body."""
    front = yaml.safe_dump(frontmatter, sort_keys=True, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{body}\n"


def export_adr(decision: Decision, *, vault: Path) -> Path:
    """Write one decision as an ADR note at ``AXON/Decisions/<id>.md``.

    Metadata lives in YAML frontmatter (filter facets, not embedded); the body
    is the summary as an H1 plus optional ``#tags`` and ``[[related]]`` links.
    """
    frontmatter: dict[str, object] = {
        "id": decision.id,
        "status": decision.status,
        "repo": decision.repo,
        "agent": decision.agent,
        "timestamp": decision.timestamp.isoformat(),
        "validation_score": decision.validation_score,
        "git_hash": decision.git_hash or "",
        "files": [f.as_posix() for f in decision.files],
        "symbols": list(decision.symbols),
    }
    body_lines = [f"# {decision.summary}"]
    if decision.tags:
        body_lines += ["", " ".join(f"#{t}" for t in decision.tags)]
    if decision.linked_decisions:
        related = " ".join(f"[[{d}]]" for d in decision.linked_decisions)
        body_lines += ["", f"**Related:** {related}"]
    content = _render_note(frontmatter, "\n".join(body_lines))
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
