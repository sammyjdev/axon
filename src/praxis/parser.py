"""Markdown spec parser.

Turns a structured Markdown document into a :class:`ParsedSpec` — a title, a
goal statement, and an ordered list of :class:`Subtask` objects. The format is
intentionally small:

* ``# Heading`` — the spec title.
* ``> Goal: ...`` or ``Goal: ...`` — the goal statement.
* ``### N. Title`` — one subtask; the leading number becomes its id.
* ``depends_on: a, b`` inside a subtask — dependency ids.

Everything else under a subtask heading becomes its description.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from praxis.state import Subtask

_H1 = re.compile(r"^#\s+(.*\S)\s*$")
_H2 = re.compile(r"^##\s+(.*\S)\s*$")
_H3 = re.compile(r"^###\s+(.*\S)\s*$")
_NUM = re.compile(r"^\s*(\d+)\s*[.)\-:]\s*(.*\S)\s*$")
_GOAL = re.compile(r"^\s*>?\s*\**\s*goal\s*\**\s*[:=]\s*(.+\S)\s*$", re.IGNORECASE)
_DEPENDS = re.compile(
    r"^\s*[-*>]?\s*`?\s*depends_on\s*`?\s*[:=]\s*(.+\S)\s*$", re.IGNORECASE
)
_QUOTE = re.compile(r"^\s*>\s+(.+\S)\s*$")


@dataclass
class ParsedSpec:
    """Structured result of parsing a Markdown task spec."""

    title: str
    goal: str
    subtasks: list[Subtask] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "goal": self.goal,
            "subtasks": [s.to_dict() for s in self.subtasks],
        }


@dataclass
class _Pending:
    id: str
    title: str
    desc: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


def _parse_ids(raw: str) -> list[str]:
    cleaned = raw.replace("`", " ").replace("#", " ")
    return [part for part in re.split(r"[,\s]+", cleaned.strip()) if part]


def parse_spec(text: str) -> ParsedSpec:
    """Parse a Markdown task spec into a :class:`ParsedSpec`."""

    title = ""
    goal = ""
    subtasks: list[Subtask] = []
    pending: _Pending | None = None
    seq = 0

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        subtasks.append(
            Subtask(
                id=pending.id,
                title=pending.title,
                description="\n".join(pending.desc).strip(),
                depends_on=pending.depends_on,
            )
        )
        pending = None

    for line in text.splitlines():
        h3 = _H3.match(line)
        if h3:
            flush()
            seq += 1
            heading = h3.group(1).strip()
            num = _NUM.match(heading)
            if num:
                pending = _Pending(id=num.group(1), title=num.group(2).strip())
            else:
                pending = _Pending(id=str(seq), title=heading)
            continue

        h1 = _H1.match(line)
        if h1 and not title:
            title = h1.group(1).strip()
            continue

        if _H2.match(line):
            flush()
            continue

        if pending is not None:
            dep = _DEPENDS.match(line)
            if dep:
                pending.depends_on.extend(_parse_ids(dep.group(1)))
                continue
            if line.strip():
                pending.desc.append(line.strip())
            continue

        if not goal:
            goal_match = _GOAL.match(line)
            if goal_match:
                goal = goal_match.group(1).strip()
                continue
            quote = _QUOTE.match(line)
            if quote:
                goal = quote.group(1).strip()

    flush()
    if not goal:
        goal = title
    return ParsedSpec(title=title, goal=goal, subtasks=subtasks)
