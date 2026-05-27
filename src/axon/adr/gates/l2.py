"""L2 lexical overlap gate (dec-111).

Validates that the ADR rationale references at least ``min_overlap``
non-stopword tokens that also appear in ``diff ∪ commit_msg_body``.
Pool includes both because abstract ADRs ("migrate to repository
pattern") have rationale whose token base is the commit body, not the
diff, which only carries imports/interfaces.

Boilerplate tokens (ticket IDs, GitHub trailers, conventional commit
types) are stripped via a denylist before the count.
"""

from __future__ import annotations

import re

# Common stopwords (English + Portuguese minimal set). Not exhaustive —
# enough to filter the most aggressive false-positives in overlap counts.
_STOPWORDS: frozenset[str] = frozenset(
    {
        # English
        "the", "a", "an", "and", "or", "but", "of", "in", "on", "at",
        "to", "for", "with", "by", "from", "as", "is", "are", "was",
        "were", "be", "been", "being", "have", "has", "had", "do",
        "does", "did", "this", "that", "these", "those", "it", "its",
        "we", "our", "us", "you", "your", "they", "them", "their",
        "i", "me", "my", "he", "him", "his", "she", "her", "if",
        "then", "else", "so", "than", "into", "out", "up", "down",
        "over", "under", "not", "no", "yes",
        # Portuguese
        "o", "a", "os", "as", "um", "uma", "de", "do", "da", "dos",
        "das", "no", "na", "nos", "nas", "e", "ou", "que", "para",
        "por", "com", "se", "sem", "ser", "ter", "foi", "são", "é",
        "isso", "isto", "aquilo", "ele", "ela", "eles", "elas",
        "esse", "essa", "este", "esta",
    }
)


def _denylist_patterns() -> list[re.Pattern[str]]:
    """Patterns whose matches are stripped from the pool before tokenising."""
    return [
        re.compile(r"\b[A-Z][A-Z0-9_]{2,}-\d+\b"),  # JIRA-1234
        re.compile(r"#\d+\b"),                       # #123
        re.compile(r"(?im)^(co-authored-by|signed-off-by):.*$"),
        # Conventional commit type prefix at line start (subject we already
        # consumed elsewhere, but body lines can carry them too)
        re.compile(
            r"(?im)^(feat|fix|chore|docs|refactor|perf|test|build|ci|style|revert)"
            r"(?:\([^)]+\))?!?:\s*"
        ),
    ]


def _strip_boilerplate(text: str) -> str:
    out = text
    for pat in _denylist_patterns():
        out = pat.sub(" ", out)
    return out


def tokenize(text: str) -> list[str]:
    """Lowercase tokenizer dropping stopwords and tokens shorter than 3 chars."""
    cleaned = _strip_boilerplate(text)
    raw = re.findall(r"[A-Za-zÀ-ÿ0-9_]+", cleaned)
    return [t.lower() for t in raw if len(t) >= 3 and t.lower() not in _STOPWORDS]


def overlap_count(rationale: str, *, pool_text: str) -> int:
    """Number of distinct rationale tokens present in ``pool_text``."""
    pool = set(tokenize(pool_text))
    rationale_tokens = set(tokenize(rationale))
    return len(rationale_tokens & pool)


def passes_l2(
    rationale: str,
    *,
    pool_text: str,
    min_overlap: int = 3,
) -> tuple[bool, int]:
    """Return ``(passed, count)`` — count is the actual overlap measured."""
    count = overlap_count(rationale, pool_text=pool_text)
    return count >= min_overlap, count
