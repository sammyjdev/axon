from __future__ import annotations

import importlib
import logging
from unittest.mock import MagicMock, patch

import pytest


def test_detect_providers_cuda() -> None:
    """CUDA desktop: _detect_providers returns CUDA first."""
    cuda_providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    with (
        patch("onnxruntime.get_available_providers", return_value=cuda_providers),
        patch("platform.system", return_value="Linux"),
        patch("platform.machine", return_value="x86_64"),
    ):
        from axon.embedder import engine as eng
        importlib.reload(eng)
        result = eng._detect_providers()
    assert result == ["CUDAExecutionProvider", "CPUExecutionProvider"], f"Got {result}"


def test_detect_providers_cpu_fallback() -> None:
    """CPU-only machine: _detect_providers returns only CPU."""
    with (
        patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]),
        patch("platform.system", return_value="Linux"),
        patch("platform.machine", return_value="x86_64"),
    ):
        from axon.embedder import engine as eng
        importlib.reload(eng)
        result = eng._detect_providers()
    assert result == ["CPUExecutionProvider"], f"Got {result}"


def test_detect_providers_coreml_mac() -> None:
    """Apple Silicon Mac: _detect_providers returns CoreML first."""
    coreml_providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    with (
        patch("onnxruntime.get_available_providers", return_value=coreml_providers),
        patch("platform.system", return_value="Darwin"),
        patch("platform.machine", return_value="arm64"),
    ):
        from axon.embedder import engine as eng
        importlib.reload(eng)
        result = eng._detect_providers()
    assert result == ["CoreMLExecutionProvider", "CPUExecutionProvider"], f"Got {result}"


def test_detect_providers_darwin_x86_no_coreml() -> None:
    """Intel Mac without CoreML: falls back to CPU even on Darwin."""
    with (
        patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]),
        patch("platform.system", return_value="Darwin"),
        patch("platform.machine", return_value="x86_64"),
    ):
        from axon.embedder import engine as eng
        importlib.reload(eng)
        result = eng._detect_providers()
    assert result == ["CPUExecutionProvider"], f"Got {result}"


def test_ensure_model_lazy_init() -> None:
    """_ensure_model must instantiate TextEmbedding only once (lazy init)."""
    from axon.embedder.engine import EmbedderEngine

    mock_model = MagicMock()
    # Simulate bound providers for the verification step
    mock_model.model.model.get_providers.return_value = ["CPUExecutionProvider"]

    with (
        patch("axon.embedder.engine._detect_providers", return_value=["CPUExecutionProvider"]),
        patch("axon.embedder.engine.TextEmbedding", return_value=mock_model) as mock_cls,
    ):
        eng = EmbedderEngine()
        _ = eng._ensure_model()
        _ = eng._ensure_model()
        assert mock_cls.call_count == 1, "TextEmbedding must be instantiated only once"


def test_ensure_model_warns_on_silent_cpu_fallback(caplog: pytest.LogCaptureFixture) -> None:
    """If requested providers include CUDA but bound providers are CPU-only, a warning is logged."""
    from axon.embedder.engine import EmbedderEngine

    mock_model = MagicMock()
    mock_model.model.model.get_providers.return_value = ["CPUExecutionProvider"]

    cuda_providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    with (
        patch("axon.embedder.engine._detect_providers", return_value=cuda_providers),
        patch("axon.embedder.engine.TextEmbedding", return_value=mock_model),
        caplog.at_level(logging.WARNING, logger="axon.embedder.engine"),
    ):
        eng = EmbedderEngine()
        eng._ensure_model()

    def _has_cpu_warning(msg: str) -> bool:
        return "silent" in msg or "fallback" in msg or "cpu" in msg

    assert any(_has_cpu_warning(rec.message.lower()) for rec in caplog.records), (
        f"Expected a warning about CPU fallback, got: {[r.message for r in caplog.records]}"
    )


def test_preload_dlls_called_on_module_import() -> None:
    """preload_dlls() must be called exactly once at module import time when hasattr is True.

    Mocks onnxruntime.preload_dlls (adding it if absent), reloads the engine module,
    and asserts the mock was called exactly once. If the real ort lacks the attribute,
    asserts the mock was NOT called (guard kept it safe).
    """
    import onnxruntime as ort

    from axon.embedder import engine as eng_module

    real_has_preload = hasattr(ort, "preload_dlls")

    with patch.object(ort, "preload_dlls", create=True) as mock_preload:
        importlib.reload(eng_module)
        if real_has_preload:
            mock_preload.assert_called_once()
        else:
            mock_preload.assert_not_called()


def test_idempotencia_provider_fallback() -> None:
    """Calling _detect_providers twice returns the same result (idempotent)."""
    with (
        patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]),
        patch("platform.system", return_value="Linux"),
        patch("platform.machine", return_value="x86_64"),
    ):
        from axon.embedder import engine as eng
        importlib.reload(eng)
        result1 = eng._detect_providers()
        result2 = eng._detect_providers()
    assert result1 == result2, f"Expected idempotent results, got {result1} vs {result2}"
