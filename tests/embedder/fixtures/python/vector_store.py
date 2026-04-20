from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    id: str
    score: float
    payload: dict[str, Any]


class VectorStore:
    def __init__(self, collection: str, host: str = "localhost", port: int = 6333) -> None:
        self.collection = collection
        self.host = host
        self.port = port
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            from qdrant_client import QdrantClient
            self._client = QdrantClient(host=self.host, port=self.port)

    def search(self, vector: list[float], limit: int = 10) -> list[SearchResult]:
        self._ensure_client()
        hits = self._client.search(
            collection_name=self.collection,
            query_vector=vector,
            limit=limit,
        )
        return [SearchResult(id=str(h.id), score=h.score, payload=h.payload or {}) for h in hits]

    def upsert(self, doc_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        self._ensure_client()
        from qdrant_client.models import PointStruct
        self._client.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=doc_id, vector=vector, payload=payload)],
        )

    def delete(self, doc_id: str) -> None:
        self._ensure_client()
        from qdrant_client.models import PointIdsList
        self._client.delete(
            collection_name=self.collection,
            points_selector=PointIdsList(points=[doc_id]),
        )
