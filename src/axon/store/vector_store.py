import os
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from axon.context.staleness import assess_staleness, detect_stale_replacements
from axon.context.registry import VALID_CONTEXTS

VECTOR_SIZE = int(os.environ.get("AXON_VECTOR_SIZE", "384"))
_STALE_RANKING_PENALTY = 0.2

COLLECTIONS = list(VALID_CONTEXTS)


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


class VectorStore:
    def __init__(self, url: str = "http://localhost:6333") -> None:
        self._client = AsyncQdrantClient(url=url)

    async def ensure_collections(self) -> None:
        existing = {c.name for c in (await self._client.get_collections()).collections}
        for name in COLLECTIONS:
            if name not in existing:
                await self._client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
                )
                continue

            info = await self._client.get_collection(name)
            vectors_cfg = info.config.params.vectors
            current_size: int | None = None

            if isinstance(vectors_cfg, dict):
                default_cfg = vectors_cfg.get("") or next(iter(vectors_cfg.values()), None)
                current_size = getattr(default_cfg, "size", None)
            else:
                current_size = getattr(vectors_cfg, "size", None)

            if current_size != VECTOR_SIZE:
                await self._client.delete_collection(collection_name=name)
                await self._client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
                )

    async def upsert(self, chunk: Chunk) -> None:
        point = PointStruct(
            id=chunk.id,
            vector=chunk.vector,
            payload={
                "file_path": chunk.file_path,
                "language": chunk.language,
                "chunk_type": chunk.chunk_type,
                "symbol": chunk.symbol,
                "project": chunk.project,
                "content": chunk.content,
                "git_commit": chunk.git_commit,
                "modified_at": chunk.modified_at.isoformat(),
            },
        )
        await self._client.upsert(collection_name=chunk.ctx, points=[point])

    async def upsert_batch(self, chunks: list[Chunk]) -> None:
        # Group by ctx to minimise round-trips
        by_ctx: dict[str, list[PointStruct]] = {}
        for chunk in chunks:
            point = PointStruct(
                id=chunk.id,
                vector=chunk.vector,
                payload={
                    "file_path": chunk.file_path,
                    "language": chunk.language,
                    "chunk_type": chunk.chunk_type,
                    "symbol": chunk.symbol,
                    "project": chunk.project,
                    "content": chunk.content,
                    "git_commit": chunk.git_commit,
                    "modified_at": chunk.modified_at.isoformat(),
                },
            )
            by_ctx.setdefault(chunk.ctx, []).append(point)

        for ctx, points in by_ctx.items():
            await self._client.upsert(collection_name=ctx, points=points)

    async def search(
        self,
        query_vector: list[float],
        collections: list[str],
        language: str | None = None,
        project: str | None = None,
        top_k: int = 5,
        max_depth: int = 1,
        max_nodes: int = 25,
        max_tokens: int = 1200,
    ) -> list[dict]:
        _ = max_depth
        conditions: list[FieldCondition] = []
        if language:
            conditions.append(FieldCondition(key="language", match=MatchValue(value=language)))
        if project:
            conditions.append(FieldCondition(key="project", match=MatchValue(value=project)))

        query_filter = Filter(must=conditions) if conditions else None

        results: list[dict] = []
        for col in collections:
            response = await self._client.query_points(
                collection_name=col,
                query=query_vector,
                query_filter=query_filter,
                limit=top_k,
            )
            for hit in response.points:
                results.append({"score": hit.score, "payload": hit.payload, "id": hit.id})

        results = _apply_staleness_ranking(results, now=_utcnow())
        limited: list[dict] = []
        token_budget = max_tokens
        for item in results:
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

    async def delete_by_file(self, ctx: str, file_path: str) -> None:
        await self._client.delete(
            collection_name=ctx,
            points_selector=Filter(
                must=[FieldCondition(key="file_path", match=MatchValue(value=file_path))]
            ),
        )

    async def close(self) -> None:
        await self._client.close()


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


def _utcnow() -> datetime:
    return datetime.now(UTC)
