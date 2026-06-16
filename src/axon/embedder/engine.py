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

# Static dimension map — avoids loading any model just to learn its output size.
# Add entries here when new models are introduced.
FASTEMBED_MODEL_DIMS: dict[str, int] = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
}


def _default_model() -> str:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return _DEFAULT_MODEL_APPLE
    return _DEFAULT_MODEL_OTHER


def default_embedding_dimension() -> int:
    """Return the vector dimension of the platform-default model without loading it."""
    return FASTEMBED_MODEL_DIMS[_default_model()]


@dataclass
class EmbedderEngine:
    model_name: str = field(default_factory=_default_model)
    cache_dir: Path = field(
        default_factory=lambda: Path.home() / ".cache" / "axon" / "models"
    )
    _model: TextEmbedding | None = field(default=None, init=False, repr=False)

    @property
    def dimension(self) -> int:
        """Vector dimension for this engine's model, resolved without loading the model.

        Raises KeyError for unknown model names so misconfiguration is caught early.
        """
        try:
            return FASTEMBED_MODEL_DIMS[self.model_name]
        except KeyError:
            raise KeyError(
                f"Unknown fastembed model {self.model_name!r}. "
                f"Add it to FASTEMBED_MODEL_DIMS in axon/embedder/engine.py."
            ) from None

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
