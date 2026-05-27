"""Architectural lexicon loader for dec-111 density gate.

The lexicon is a curated set of tokens that signal genuine architectural
commentary (verbs of intent, structural nouns, quality attributes). The
default ships in ``src/axon/data/architectural_lexicon.txt``; users can
override via ``axon.toml#adr.lexicon_path``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DEFAULT_PATH = Path(__file__).parent.parent / "data" / "architectural_lexicon.txt"


def load_lexicon(path: Path | None = None) -> frozenset[str]:
    """Return the lexicon as a lower-cased frozenset of tokens.

    Comment lines (``# …``) and blank lines are ignored.
    """
    target = path or _DEFAULT_PATH
    tokens: set[str] = set()
    for raw in target.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens.add(stripped.lower())
    return frozenset(tokens)


@lru_cache(maxsize=4)
def default_lexicon() -> frozenset[str]:
    """Cached default lexicon for the hot path."""
    return load_lexicon()
