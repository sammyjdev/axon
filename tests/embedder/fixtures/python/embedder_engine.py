from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EmbedResult:
    text: str
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


class EmbedderEngine:
    def __init__(self, model_name: str = "Snowflake/snowflake-arctic-embed-l-v2.0") -> None:
        self._model_name = model_name
        self._model = None

    def _ensure_model(self) -> None:
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name=self._model_name)

    def embed_one(self, text: str) -> list[float]:
        self._ensure_model()
        vectors = list(self._model.embed([text]))
        return vectors[0].tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()
        return [v.tolist() for v in self._model.embed(texts)]

    def embed_file(self, path: Path, chunks: list[str]) -> list[EmbedResult]:
        vectors = self.embed_batch(chunks)
        return [
            EmbedResult(text=chunk, vector=vec, metadata={"file_path": str(path)})
            for chunk, vec in zip(chunks, vectors)
        ]


def chunk_text(text: str, max_tokens: int = 512) -> list[str]:
    max_chars = max_tokens * 4
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        if end < len(text):
            boundary = text.rfind("\n", start, end)
            if boundary > start:
                end = boundary
        chunks.append(text[start:end])
        start = end
    return chunks
