"""Soft supersession for recall ranking.

Borrowed from EpochDB's subject-predicate + ``0.0001x`` penalty idea
(see ``docs/decisions/dec-115-supersession-ranking-penalty.md``) and adapted
to AXON's coarse-grained ``Decision`` unit: a stale decision is demoted in the
ranking rather than deleted, so it stays losslessly recallable.

The embedding-backed pairwise-similarity seam lives here so the recall strategy
stays decoupled from any concrete embedder. It runs fully offline through the
local ``fastembed`` model — no cloud calls, no cost.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from math import sqrt
from typing import Protocol

# Given two decision summaries, return their semantic similarity in [-1, 1].
# This is the seam through which an embedder is injected into recall.
PairwiseSimilarity = Callable[[str, str], float]

# A revision verb in the *newer* decision's summary signals that it replaces or
# removes — the discriminator that mid-range similarity alone cannot give, since
# additive work in the same area is also topically similar (dec-115 false-positive
# analysis). Strict list only: ambiguous verbs ("fix"/"corrigir"/"refactor")
# fire on additive fixes and are deliberately excluded.
_REVISION_VERBS = frozenset(
    {
        # English
        "drop", "dropped", "replace", "replaces", "replaced", "revert",
        "reverts", "reverted", "migrate", "migrates", "migrated", "deprecate",
        "deprecates", "deprecated", "remove", "removes", "removed", "supersede",
        "supersedes", "superseded", "rename", "renames", "renamed", "retire",
        "retired", "rewrite", "rewrites", "rewrote", "swap", "swaps", "swapped",
        "disable", "disables", "disabled",
        # Portuguese
        "substitui", "substituir", "substituiu", "troca", "trocar", "trocou",
        "remover", "removeu", "renomeia", "renomear", "renomeou", "desativa",
        "desativar", "desativou", "desabilita", "desabilitar", "reverte",
        "reverter", "reverteu", "migra", "migrar", "migrou", "descontinua",
        "descontinuar", "aposenta", "aposentar", "refaz", "refazer",
    }
)


def has_revision_verb(summary: str) -> bool:
    """True if ``summary`` carries a strict revision verb (EN/PT).

    Distinguishes 'this replaces/removes the older' from additive work that
    merely shares scope and topic.
    """
    return bool(set(re.findall(r"[a-zà-ÿ]+", summary.lower())) & _REVISION_VERBS)


class _Embedder(Protocol):
    def embed_one(self, text: str) -> list[float]: ...


def _cosine(left: list[float], right: list[float]) -> float:
    num = sum(x * y for x, y in zip(left, right, strict=False))
    na = sqrt(sum(x * x for x in left))
    nb = sqrt(sum(y * y for y in right))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return num / (na * nb)


def make_embedding_similarity(embedder: _Embedder) -> PairwiseSimilarity:
    """Adapt an embedder's ``embed_one`` into a cosine ``PairwiseSimilarity``.

    Isolates the embedder from the recall strategy: the strategy only knows the
    ``PairwiseSimilarity`` signature, never the embedding implementation.
    """

    def similarity(left: str, right: str) -> float:
        return _cosine(embedder.embed_one(left), embedder.embed_one(right))

    return similarity
