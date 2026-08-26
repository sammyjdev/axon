from __future__ import annotations

import logging
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from axon.embedder.providers import embed_via_chain

if TYPE_CHECKING:
    import onnxruntime as _ort
    from fastembed import TextEmbedding
else:
    # onnxruntime/fastembed load a C++ runtime (onnxruntime_pybind11_state.so)
    # that every axon CLI process was paying for at import time even when it
    # never creates an ONNX session (the default model routes through
    # embed_via_chain, an HTTP path with no ONNX involvement). That C++
    # runtime's exit-time static-destructor teardown is what crashed
    # interpreter shutdown with "libc++abi ... recursive_mutex lock failed"
    # (issue #149). Both names are populated lazily, on first real use, by
    # _ensure_model()/_detect_providers() - the only places that need them.
    _ort = None
    TextEmbedding = None

logger = logging.getLogger(__name__)

# Model name that routes embed()/embed_one() to the bge-m3 provider chain
# (Ollama -> NIM -> DeepInfra) instead of the in-process fastembed/onnx path.
# This is the DEFAULT model as of EMB-3 (dim 1024).
_CHAIN_MODEL_NAME = "bge-m3"

# Static dimension map - avoids loading any model just to learn its output size.
# Add entries here when new models are introduced.
FASTEMBED_MODEL_DIMS: dict[str, int] = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    _CHAIN_MODEL_NAME: 1024,
}


def _default_model() -> str:
    """The bge-m3 chain is the default on every platform (EMB-3).

    The former platform-conditional fastembed defaults (bge-small-en-v1.5 on
    Apple Silicon, bge-base-en-v1.5 elsewhere) are still selectable explicitly
    via EmbedderEngine(model_name=...); they are just no longer the default.
    """
    return _CHAIN_MODEL_NAME


def default_embedding_dimension() -> int:
    """Return the vector dimension of the platform-default model without loading it."""
    return FASTEMBED_MODEL_DIMS[_default_model()]


def _detect_providers() -> list[str]:
    """Auto-detect the best ONNX execution provider for this machine.

    Priority: CUDAExecutionProvider (NVIDIA GPU) -> CoreMLExecutionProvider
    (Apple Silicon) -> CPUExecutionProvider (universal fallback).

    Imports onnxruntime lazily (see the module-level comment on _ort) so
    calling this does not pull the C++ runtime into a process that never
    needed it.
    """
    global _ort
    if _ort is None:
        import onnxruntime as _ort
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
            global _ort, TextEmbedding
            if _ort is None:
                import onnxruntime as _ort
            # preload_dlls() must run before the session is constructed so
            # pip-installed nvidia-cudnn-cu12 / nvidia-cublas-cu12 /
            # nvidia-cuda-runtime-cu12 DLLs are on the DLL search path before
            # any ONNX session is created. Guarded by hasattr because
            # CPU-only onnxruntime builds do not expose this function.
            if hasattr(_ort, "preload_dlls"):
                try:
                    _ort.preload_dlls()
                    logger.debug("onnxruntime.preload_dlls() succeeded")
                except Exception as _exc:  # noqa: BLE001
                    logger.warning("onnxruntime.preload_dlls() failed: %s", _exc)
            if TextEmbedding is None:
                from fastembed import TextEmbedding

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
        """Embeds a list of texts. Returns one vector per text.

        When model_name is "bge-m3", routes through the configured provider
        chain (Ollama -> NIM -> DeepInfra); otherwise uses fastembed unchanged.
        """
        if self.model_name == _CHAIN_MODEL_NAME:
            return embed_via_chain(texts)
        model = self._ensure_model()
        return [vec.tolist() for vec in model.embed(texts)]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
