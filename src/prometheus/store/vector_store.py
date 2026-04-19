from dataclasses import dataclass, field
from datetime import datetime

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)


VECTOR_SIZE = 1024  # snowflake-arctic-embed-l-v2.0

COLLECTIONS = ["personal", "career", "knowledge", "work"]


@dataclass
class Chunk:
    id: str
    vector: list[float]
    file_path: str
    language: str
    chunk_type: str          # method | class | file
    symbol: str
    project: str
    ctx: str                 # personal | career | knowledge | work
    content: str
    git_commit: str = ""
    modified_at: datetime = field(default_factory=datetime.utcnow)


class VectorStore:
    def __init__(self, host: str = "localhost", port: int = 6333) -> None:
        self._client = AsyncQdrantClient(host=host, port=port)

    async def ensure_collections(self) -> None:
        existing = {c.name for c in (await self._client.get_collections()).collections}
        for name in COLLECTIONS:
            if name not in existing:
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
    ) -> list[dict]:
        conditions: list[FieldCondition] = []
        if language:
            conditions.append(FieldCondition(key="language", match=MatchValue(value=language)))
        if project:
            conditions.append(FieldCondition(key="project", match=MatchValue(value=project)))

        query_filter = Filter(must=conditions) if conditions else None

        results: list[dict] = []
        for col in collections:
            hits = await self._client.search(
                collection_name=col,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=top_k,
            )
            for hit in hits:
                results.append({"score": hit.score, "payload": hit.payload, "id": hit.id})

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    async def delete_by_file(self, ctx: str, file_path: str) -> None:
        await self._client.delete(
            collection_name=ctx,
            points_selector=Filter(
                must=[FieldCondition(key="file_path", match=MatchValue(value=file_path))]
            ),
        )

    async def close(self) -> None:
        await self._client.close()
