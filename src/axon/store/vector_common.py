"""Common vector-store helpers.

Delta recall uses a structural cutoff: a chunk 60%+ covered by the
conversation transcript is considered already known.
"""

import os
from datetime import datetime

from pydantic import BaseModel, Field

from axon.context.staleness import assess_staleness, detect_stale_replacements
from axon.embedder.engine import default_embedding_dimension

# AXON_VECTOR_SIZE overrides the default; the default is derived from the
# platform-selected embedder model so collections always match embedding output.
VECTOR_SIZE = int(os.environ.get("AXON_VECTOR_SIZE", default_embedding_dimension()))
# Read at process start (house convention for tuning knobs, like VECTOR_SIZE);
# the on/off flag AXON_DELTA_RECALL is read per-request in http/app.py.
DELTA_RECALL_CUTOFF = float(os.environ.get("AXON_DELTA_RECALL_CUTOFF", "0.6"))
_STALE_RANKING_PENALTY = 0.2


def transcript_shingle_set(transcript: list[str]) -> set[str]:
    """Union of shingles over all transcript entries - compute ONCE per request."""
    shingles: set[str] = set()
    for entry in transcript:
        shingles.update(_char_shingles(_normalize_ws(entry)))
    return shingles


def shingles_cover(chunk_text: str, transcript_shingles: set[str], *, cutoff: float = 0.6) -> bool:
    chunk_shingles = _char_shingles(_normalize_ws(chunk_text))
    if not chunk_shingles or not transcript_shingles:
        return False
    covered = len(chunk_shingles & transcript_shingles)
    return covered / len(chunk_shingles) >= cutoff


def transcript_covers(chunk_text: str, transcript: list[str], *, cutoff: float = 0.6) -> bool:
    if not transcript:
        return False
    return shingles_cover(chunk_text, transcript_shingle_set(transcript), cutoff=cutoff)


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def _char_shingles(text: str, *, k: int = 40, step: int = 20) -> set[str]:
    if not text:
        return set()
    if len(text) <= k:
        return {text}
    return {text[start : start + k] for start in range(0, len(text) - k + 1, step)}


class Chunk(BaseModel):
    id: str
    vector: list[float]
    file_path: str
    language: str
    chunk_type: str  # method | class | file
    symbol: str
    project: str
    ctx: str  # personal | career | knowledge | saas | work
    content: str
    git_commit: str = ""
    modified_at: datetime = Field(default_factory=datetime.utcnow)


def _rank_and_limit(
    results: list[dict],
    *,
    top_k: int,
    max_nodes: int,
    max_tokens: int,
    now: datetime,
) -> list[dict]:
    """Used by the pgvector backend to rank and truncate search hits."""
    ranked = _apply_staleness_ranking(results, now=now)
    limited: list[dict] = []
    token_budget = max_tokens
    for item in ranked:
        payload = item.get("payload") or {}
        content = str(payload.get("content", ""))
        estimated = max(1, len(content) // 4)
        if len(limited) >= max_nodes:
            break
        # ponytail: always keep the top hit even if it alone exceeds the token
        # budget; the budget cap only prunes the tail, it must never turn a real
        # hit into an empty result (EMB-4 regression).
        if limited and token_budget - estimated < 0:
            break
        token_budget -= estimated
        limited.append(item)
    return limited[:top_k]


def _apply_staleness_ranking(results: list[dict], *, now: datetime) -> list[dict]:
    records = [_staleness_record(result) for result in results]
    replacements = {
        replacement.stale_id: replacement
        for replacement in detect_stale_replacements(records, now=now)
    }

    ranked_results: list[dict] = []
    for result in results:
        payload = result.get("payload") or {}
        assessment = assess_staleness(payload, now=now)
        replacement = replacements.get(str(result.get("id", "")))
        ranking_score = float(result["score"]) - (assessment.score * _STALE_RANKING_PENALTY)

        ranked_results.append(
            {
                **result,
                "ranking_score": ranking_score,
                "staleness": {
                    "score": assessment.score,
                    "is_stale": assessment.is_stale,
                    "reasons": list(assessment.reasons),
                    "replacement_family": assessment.replacement_family,
                    "replacement_id": replacement.replacement_id if replacement else None,
                    "replacement_reason": replacement.reason if replacement else None,
                },
            }
        )

    ranked_results.sort(
        key=lambda result: (
            -float(result["ranking_score"]),
            -float(result["score"]),
            str(result.get("id", "")),
        )
    )
    return ranked_results


def _staleness_record(result: dict) -> dict[str, object]:
    payload = dict(result.get("payload") or {})
    payload["id"] = str(result.get("id", ""))
    return payload
