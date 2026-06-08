"""Unified context recall.

`recall_context` merges decisions surfaced by several sources, ranks them by
recency, semantic relevance and validation score, and returns a compact
summary truncated to a token budget.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from axon.core.decision import Decision
from axon.recall.supersession import PairwiseSimilarity, has_revision_verb
from axon.store.session_store import SessionStore

logger = logging.getLogger(__name__)

# A semantic-search hook: given a query string, returns (decision_id, summary,
# score in 0..1) tuples. Optional — when absent, semantic recall is skipped.
# This is the seam for mem0; an adapter conforms to this signature.
SemanticSearch = Callable[[str], Awaitable[Sequence[tuple[str, str, float]]]]

_W_RECENCY = 0.4
_W_SEMANTIC = 0.4
_W_VALIDATION = 0.2
_RECENCY_HALFLIFE_DAYS = 14.0
_REPO_LIMIT = 30

# Soft-supersession (opt-in, default off). A stale decision keeps its place in
# the result but its rank is multiplied by this near-zero factor, mirroring
# EpochDB's 0.0001x penalty: demote, never delete. See dec-115.
_SUPERSESSION_PENALTY = 0.02
# Two decisions in the same scope must be at least this cosine-similar to be
# *candidates* for supersession — the topical floor that rejects unrelated edits
# to a shared file. Passing it is necessary but not sufficient (additive work in
# the same area also clears it); a revision verb or near-duplicate confirms.
_SCOPE_SIM_THRESHOLD = 0.82
# Above this similarity the pair is a near-duplicate (a reworded restatement),
# strong enough to infer supersession without a revision verb. Below it, an
# explicit revision verb in the newer summary is required. Calibrated against the
# dec-115 real-data false-positive analysis; revisit as labelled cases accrue.
_NEAR_DUP_THRESHOLD = 0.93


@dataclass
class _Candidate:
    """Mutable accumulator: one decision, best signal seen across sources."""

    decision_id: str
    summary: str
    recency: float = 0.0
    semantic: float = 0.0
    validation: float = 0.0
    sources: set[str] = field(default_factory=set)
    # The underlying decision, when this candidate came from SQLite. Semantic-only
    # candidates (surfaced by mem0) have no Decision and are exempt from
    # supersession detection.
    decision: Decision | None = None
    superseded: bool = False

    @property
    def rank(self) -> float:
        base = (
            _W_RECENCY * self.recency
            + _W_SEMANTIC * self.semantic
            + _W_VALIDATION * self.validation
        )
        return base * (_SUPERSESSION_PENALTY if self.superseded else 1.0)


def _recency(timestamp: datetime, now: datetime) -> float:
    """Exponential-decay recency score in (0, 1]; 1.0 today, 0.5 at half-life."""
    age_days = max((now - timestamp).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (age_days / _RECENCY_HALFLIFE_DAYS)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


async def recall_context(
    repo: str,
    files: list[str] | None = None,
    symbols: list[str] | None = None,
    *,
    store: SessionStore,
    semantic_search: SemanticSearch | None = None,
    token_budget: int = 2000,
    enable_supersession: bool = False,
    similarity: PairwiseSimilarity | None = None,
    similarity_threshold: float = _SCOPE_SIM_THRESHOLD,
    near_dup_threshold: float = _NEAR_DUP_THRESHOLD,
) -> str:
    """Recall a compact, ranked context summary for a repo.

    Sources merged: recent decisions for ``repo`` (SQLite), decisions touching
    any of ``symbols`` (SQLite), and — when ``semantic_search`` is supplied —
    semantically similar decisions (e.g. mem0). The semantic source degrades
    gracefully: a failure contributes nothing rather than raising. SQLite is
    the source of truth, so its errors propagate.

    Soft supersession (``enable_supersession``, default off) demotes stale
    decisions in the ranking without dropping them (see dec-115). It is purely
    additive: with the flag off the result is byte-for-byte the legacy ranking.
    """
    files = files or []
    symbols = symbols or []
    now = datetime.now(UTC)
    candidates: dict[str, _Candidate] = {}

    def _merge(
        decision_id: str,
        summary: str,
        *,
        source: str,
        recency: float = 0.0,
        semantic: float = 0.0,
        validation: float = 0.0,
        decision: Decision | None = None,
    ) -> None:
        cand = candidates.get(decision_id)
        if cand is None:
            cand = _Candidate(decision_id, summary)
            candidates[decision_id] = cand
        cand.recency = max(cand.recency, recency)
        cand.semantic = max(cand.semantic, semantic)
        cand.validation = max(cand.validation, validation)
        cand.sources.add(source)
        if decision is not None:
            cand.decision = decision

    def _merge_decision(decision: Decision, *, source: str, semantic: float) -> None:
        file_hit = bool({str(f) for f in decision.files} & set(files))
        _merge(
            decision.id,
            decision.summary,
            source=source,
            recency=_recency(decision.timestamp, now),
            semantic=max(semantic, 0.5 if file_hit else 0.0),
            validation=decision.validation_score / 5.0,
            decision=decision,
        )

    # 1. SQLite — recent decisions for the repo.
    for decision in await store.find_decisions_by_repo(repo, limit=_REPO_LIMIT):
        _merge_decision(decision, source="repo", semantic=0.0)

    # 2. SQLite — decisions touching the queried symbols.
    for symbol in symbols:
        for decision in await store.find_decisions_by_symbol(symbol):
            _merge_decision(decision, source="symbol", semantic=1.0)

    # 3. Semantic recall (e.g. mem0) — optional, degrades gracefully.
    if semantic_search is not None:
        query = " ".join([repo, *symbols, *files])
        try:
            hits = await semantic_search(query)
        except Exception as exc:  # external source — never break recall
            logger.warning("semantic recall skipped: %s", exc)
            hits = []
        for decision_id, summary, score in hits:
            _merge(
                decision_id,
                summary,
                source="semantic",
                semantic=min(max(score, 0.0), 1.0),
            )

    if enable_supersession:
        _mark_superseded(
            candidates.values(),
            similarity=similarity,
            threshold=similarity_threshold,
            near_dup_threshold=near_dup_threshold,
        )

    ranked = sorted(candidates.values(), key=lambda c: c.rank, reverse=True)
    return _render(repo, ranked, token_budget)


def _scope(decision: Decision) -> set[str]:
    """The decision's 'subject' — the files and symbols it touches."""
    return {str(f) for f in decision.files} | set(decision.symbols)


def _mark_superseded(
    candidates: Iterable[_Candidate],
    *,
    similarity: PairwiseSimilarity | None,
    threshold: float = _SCOPE_SIM_THRESHOLD,
    near_dup_threshold: float = _NEAR_DUP_THRESHOLD,
) -> None:
    """Flag stale decisions so their rank is penalised.

    Signals, mirroring EpochDB's subject-predicate supersession:

    1. An already-``superseded`` status on the stored decision is honoured
       directly.
    2. Among decisions sharing scope (overlapping files/symbols) whose summaries
       clear the topical floor (cosine ≥ ``threshold``), the older one is
       superseded by the newer **only when the revision is confirmed**: either
       the newer summary carries a revision verb, or the pair is a near-duplicate
       (cosine ≥ ``near_dup_threshold``). The floor alone is not enough —
       additive work in the same area is also topically similar. Without a
       ``similarity`` seam, no automatic supersession is inferred.
    """
    items = [c for c in candidates if c.decision is not None]
    for cand in items:
        if cand.decision is not None and cand.decision.status == "superseded":
            cand.superseded = True

    if similarity is None:
        return

    for i, older_first in enumerate(items):
        for other in items[i + 1 :]:
            a, b = older_first.decision, other.decision
            if a is None or b is None:
                continue
            if not (_scope(a) & _scope(b)):
                continue
            sim = similarity(older_first.summary, other.summary)
            if sim < threshold:  # topical floor — same subject at all?
                continue
            stale, fresh = (
                (older_first, other)
                if a.timestamp <= b.timestamp
                else (other, older_first)
            )
            # Confirm it's a revision, not additive same-area work.
            if has_revision_verb(fresh.summary) or sim >= near_dup_threshold:
                stale.superseded = True


def _render(repo: str, ranked: list[_Candidate], token_budget: int) -> str:
    header = f"## AXON recall — {repo}"
    if not ranked:
        return f"{header}\n(no decisions recalled)"
    lines = [header]
    used = _estimate_tokens(header)
    for cand in ranked:
        line = f"- {cand.decision_id} (rank {cand.rank:.2f}): {cand.summary}"
        cost = _estimate_tokens(line)
        if used + cost > token_budget:
            break
        lines.append(line)
        used += cost
    return "\n".join(lines)
