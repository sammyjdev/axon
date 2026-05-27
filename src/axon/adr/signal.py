"""Commit-message signal detector for declarative memory capture (dec-110).

ADR inference fires only when the commit explicitly signals architectural
intent. Two surfaces are recognised:

- **Subject prefix** (primary, Conventional-Commits-compatible):
  ``arch:`` or ``decision:`` at the very start of the subject line.
  Optional scope ``arch(area):`` and breaking marker ``arch!:`` work too.
- **Trailer** (metadata, opt-in): ``ADR-Decision: <title>`` line in the
  commit body. Case-insensitive. Useful for teams whose ``commitlint``
  ``type-enum`` cannot be extended.

Subject prefix takes precedence when both are present. Returns ``None``
when no signal is found — callers must early-return without invoking the
LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class SignalKind(StrEnum):
    SUBJECT_PREFIX = "subject_prefix"
    TRAILER = "trailer"


@dataclass(frozen=True)
class Signal:
    """An architectural signal extracted from a commit message."""

    kind: SignalKind
    title: str


# Subject prefix:
#   arch: ...
#   decision: ...
#   arch(scope): ...
#   arch!: ...   (breaking change marker)
_SUBJECT_PREFIX_RE = re.compile(
    r"^(?:arch|decision)(?:\([^)]+\))?!?:\s*(?P<title>\S.*?)\s*$"
)

# Trailer in body (Git trailer convention, case-insensitive):
#   ADR-Decision: <title>
_TRAILER_RE = re.compile(
    r"^ADR-Decision:\s*(?P<title>\S.*?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def detect(commit_message: str) -> Signal | None:
    """Return a ``Signal`` if the message carries an architectural marker.

    The subject (first line) is inspected for ``arch:``/``decision:``
    prefixes. If not present, the body (lines after the first blank) is
    scanned for the ``ADR-Decision:`` trailer.
    """
    if not commit_message or not commit_message.strip():
        return None

    lines = commit_message.split("\n")
    subject = lines[0]

    subject_match = _SUBJECT_PREFIX_RE.match(subject)
    if subject_match:
        title = subject_match.group("title").strip()
        if title:
            return Signal(kind=SignalKind.SUBJECT_PREFIX, title=title)

    body = _extract_body(lines)
    if body:
        trailer_match = _TRAILER_RE.search(body)
        if trailer_match:
            title = trailer_match.group("title").strip()
            if title:
                return Signal(kind=SignalKind.TRAILER, title=title)

    return None


def _extract_body(lines: list[str]) -> str:
    """Return the commit body (everything after the first blank line).

    Returns an empty string if the message has no body.
    """
    blank_idx: int | None = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "":
            blank_idx = i
            break
    if blank_idx is None:
        return ""
    return "\n".join(lines[blank_idx + 1 :])
