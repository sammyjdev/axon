from __future__ import annotations

import platform
from dataclasses import dataclass, field
from pathlib import Path

from fastembed import TextEmbedding

# Platform-aware model selection:
# - Apple Silicon: BAAI/bge-small-en-v1.5 (MPS-friendly, ~33MB)
# - GPU/CPU: BAAI/bge-base-en-v1.5 (~110MB, melhor qualidade)
_DEFAULT_MODEL_APPLE = "BAAI/bge-small-en-v1.5"
_DEFAULT_MODEL_OTHER = "BAAI/bge-base-en-v1.5"


def _default_model() -> str:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return _DEFAULT_MODEL_APPLE
    return _DEFAULT_MODEL_OTHER


@dataclass
class EmbedderEngine:
    model_name: str = field(default_factory=_default_model)
    cache_dir: Path = field(
        default_factory=lambda: Path.home() / ".cache" / "axon" / "models"
    )
    _model: TextEmbedding | None = field(default=None, init=False, repr=False)

    def _ensure_model(self) -> TextEmbedding:
        if self._model is None:
            self._model = TextEmbedding(
                model_name=self.model_name,
                cache_dir=str(self.cache_dir),
            )
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embeds a list of texts. Returns one vector per text."""
        model = self._ensure_model()
        return [vec.tolist() for vec in model.embed(texts)]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
