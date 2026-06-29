import os
from datetime import datetime

from pydantic import BaseModel, Field

from axon.context.staleness import assess_staleness, detect_stale_replacements
from axon.embedder.engine import default_embedding_dimension

# AXON_VECTOR_SIZE overrides the default; the default is derived from the
# platform-selected embedder model so collections always match embedding output.
VECTOR_SIZE = int(os.environ.get("AXON_VECTOR_SIZE", default_embedding_dimension()))
_STALE_RANKING_PENALTY = 0.2


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
        if token_budget - estimated < 0:
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
