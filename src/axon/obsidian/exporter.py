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

_STATUS_ORDER = ("active", "draft", "superseded", "deprecated")
_STATUS_HEADING = {
    "active": "Active",
    "draft": "Draft",
    "superseded": "Superseded",
    "deprecated": "Deprecated",
}


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


def _grouped_decisions(decisions: list[Decision]) -> str:
    """Body of grouped decision links: an H2 per non-empty status, in fixed
    order, with ``- [[id]] — summary`` entries. Empty input -> ``_None._``."""
    if not decisions:
        return "_None._"
    blocks: list[str] = []
    for status in _STATUS_ORDER:
        group = [d for d in decisions if d.status == status]
        if not group:
            continue
        lines = [f"## {_STATUS_HEADING[status]}", ""]
        lines += [f"- [[{d.id}]] — {d.summary}" for d in group]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "_None._"


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
    """Write an architecture overview grouping decisions by status."""
    frontmatter: dict[str, object] = {
        "kind": "architecture",
        "name": name,
        "generated": _now(),
    }
    body = f"# Architecture — {name}\n\n{_grouped_decisions(decisions)}"
    return _atomic_write(
        vault / _ROOT / _ARCHITECTURE_DIR / f"{name}.md",
        _render_note(frontmatter, body),
    )


def export_project_summary(
    repo: str, since: date, decisions: list[Decision], *, vault: Path
) -> Path:
    """Write a summary of a repo's decisions made on or after ``since``."""
    recent = [d for d in decisions if d.timestamp.date() >= since]
    frontmatter: dict[str, object] = {
        "kind": "summary",
        "repo": repo,
        "since": since.isoformat(),
        "generated": _now(),
    }
    body = f"# Summary — {repo}\n\n{_grouped_decisions(recent)}"
    return _atomic_write(
        vault / _ROOT / _SUMMARIES_DIR / f"{repo}.md",
        _render_note(frontmatter, body),
    )
