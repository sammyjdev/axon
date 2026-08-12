"""Retraction detector for declarative memory capture (dec-110 part D).

Sibling signal to the ``Lesson:`` commit trailer (``signal.py``): where the
trailer is an explicit, author-typed marker, this one looks at ordinary
assistant text for the author withdrawing a claim they previously made -
"eu estava errado", "falso alarme", and the like - and enqueues a draft
into the *same* lesson pool (``lesson_pool.py``). No second draft store,
no LLM, no write path to the corpus: this only ever proposes a draft for a
human to review.

The patterns are measured, not invented (see the PR description for the
transcript run that produced them). A false positive here costs one
deleted line in review; a false negative costs a lost lesson - so this
errs toward catching, and leaves precision to the draft pool's human
reviewer, not to more regex.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from axon.adr.lesson_pool import LessonDraftRecord
from axon.adr.lesson_pool import write_draft as _write_lesson_draft
from axon.config.data_root import data_root


@dataclass(frozen=True)
class RetractionSignal:
    """A retraction found in assistant text."""

    marker: str  # which pattern matched, for the draft title
    excerpt: str  # surrounding text, for the human reviewer's context


# Each pattern is (name, compiled regex). Checked in order; first match wins.
# Sourced from a hand-classified pass over a real transcript (26 hits, 22
# true retractions, precision ~85%) - see the PR body for the numbers.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("errei", re.compile(r"\berrei\b(?!\s+n(?:isso|esse|essa)\b)", re.IGNORECASE)),
    (
        "corrigindo",
        re.compile(r"\bcorrigindo\s+(?:o que|uma coisa que|minha)\b", re.IGNORECASE),
    ),
    ("retiro/retrato", re.compile(r"\b(?:retiro|retrato)\s+o que\b", re.IGNORECASE)),
    ("falso alarme", re.compile(r"\bfalso alarme\b", re.IGNORECASE)),
    (
        "diagnóstico mudou",
        re.compile(
            r"\b(?:meu diagnóstico|minha leitura|minha hipótese)\s+(?:estava|mudou)\b",
            re.IGNORECASE,
        ),
    ),
    # "estava errado" alone is the noisiest marker: it also matches an
    # ARTIFACT being wrong ("o que estava errado", "o teste é que estava
    # errado") rather than the author's own claim. All four measured false
    # positives shared that one signature - "que" immediately before
    # "estava" - so that's the exclusion, not a broader first-person
    # requirement that isn't validated against the transcript.
    ("estava errado", re.compile(r"\bestava errado\b", re.IGNORECASE)),
]

_EXCERPT_RADIUS = 60


def detect_retraction(text: str) -> RetractionSignal | None:
    """Return a ``RetractionSignal`` if ``text`` withdraws a prior claim.

    Scans the configured patterns in order and returns the first match.
    Returns ``None`` for empty/blank text or no match.
    """
    if not text or not text.strip():
        return None

    for name, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if name == "estava errado" and _preceded_by_que(text, match.start()):
                continue  # artifact reference ("o que estava errado"), not a claim
            return RetractionSignal(marker=name, excerpt=_excerpt(text, match))

    return None


def _preceded_by_que(text: str, match_start: int) -> bool:
    """True if the word right before ``match_start`` is "que" (cleft/relative clause)."""
    before = text[:match_start].rstrip()
    last_word = before.rsplit(maxsplit=1)[-1] if before else ""
    return last_word.strip(".,;:!?\"'").lower() == "que"


def _excerpt(text: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - _EXCERPT_RADIUS)
    end = min(len(text), match.end() + _EXCERPT_RADIUS)
    return text[start:end].strip()


def capture_retraction(
    text: str, *, source: str = "", draft_dir: Path | None = None
) -> Path | None:
    """Detect a retraction in ``text`` and enqueue a lesson draft.

    Returns the draft path, or ``None`` if no retraction was found. This
    is the one entry point a hook would call - it never touches the
    corpus or Postgres, only the same Markdown draft pool the ``Lesson:``
    trailer writes to.

    Idempotent: the draft id is derived from the text itself, so calling
    this twice with the same text does not overwrite a draft a human may
    have already started editing.
    """
    signal = detect_retraction(text)
    if signal is None:
        return None

    draft_id = "retraction-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    target_dir = draft_dir or (data_root() / "lesson-draft")
    existing = target_dir / f"{draft_id}.md"
    if existing.exists():
        return existing

    context = f"{source}\n\n{signal.excerpt}" if source else signal.excerpt
    record = LessonDraftRecord(
        commit_hash=draft_id,
        title=f"retraction ({signal.marker}): {signal.excerpt[:80]}",
        context=context,
    )
    return _write_lesson_draft(record, draft_dir=draft_dir)
