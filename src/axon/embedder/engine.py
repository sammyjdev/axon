from __future__ import annotations

import logging
import platform
from dataclasses import dataclass, field
from pathlib import Path

import onnxruntime as _ort
from fastembed import TextEmbedding

logger = logging.getLogger(__name__)

# Call preload_dlls at import time so pip-installed nvidia-cudnn-cu12 /
# nvidia-cublas-cu12 / nvidia-cuda-runtime-cu12 DLLs are on the DLL search
# path before any ONNX session is created. Guarded by hasattr because
# CPU-only onnxruntime builds do not expose this function.
if hasattr(_ort, "preload_dlls"):
    try:
        _ort.preload_dlls()
        logger.debug("onnxruntime.preload_dlls() succeeded")
    except Exception as _exc:  # noqa: BLE001
        logger.warning("onnxruntime.preload_dlls() failed: %s", _exc)

# Platform-aware model selection:
# - Apple Silicon: BAAI/bge-small-en-v1.5 (MPS-friendly, ~33MB)
# - GPU/CPU: BAAI/bge-base-en-v1.5 (~110MB, better quality)
_DEFAULT_MODEL_APPLE = "BAAI/bge-small-en-v1.5"
_DEFAULT_MODEL_OTHER = "BAAI/bge-base-en-v1.5"

# Static dimension map - avoids loading any model just to learn its output size.
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


def _detect_providers() -> list[str]:
    """Auto-detect the best ONNX execution provider for this machine.

    Priority: CUDAExecutionProvider (NVIDIA GPU) -> CoreMLExecutionProvider
    (Apple Silicon) -> CPUExecutionProvider (universal fallback).

    preload_dlls() is already called at module import time so pip-installed
    CUDA DLLs are visible when ort.get_available_providers() enumerates them.
    """
    available = set(_ort.get_available_providers())
    # Priority: CUDA -> CoreML -> CPU (CUDA wins globally, even on Darwin arm64)
    if "CUDAExecutionProvider" in available:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        if "CoreMLExecutionProvider" in available:
            return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


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
            providers = _detect_providers()
            self._model = TextEmbedding(
                model_name=self.model_name,
                cache_dir=str(self.cache_dir),
                providers=providers,
            )
            # Verify the bound provider to detect silent CPU fallback.
            # fastembed exposes the underlying onnxruntime session as model.model.model.
            try:
                bound = self._model.model.model.get_providers()
                if providers != ["CPUExecutionProvider"] and bound == ["CPUExecutionProvider"]:
                    logger.warning(
                        "Silent CPU fallback detected: requested %s but bound providers are %s. "
                        "On the CUDA desktop install: pip install onnxruntime-gpu==1.26.0 "
                        "nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-cuda-runtime-cu12",
                        providers,
                        bound,
                    )
                else:
                    logger.debug("EmbedderEngine bound providers: %s", bound)
            except AttributeError:
                logger.debug("Could not introspect bound providers (fastembed version mismatch)")
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embeds a list of texts. Returns one vector per text."""
        model = self._ensure_model()
        return [vec.tolist() for vec in model.embed(texts)]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
