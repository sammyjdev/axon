"""Smoke tests for provider detection that run without GPU hardware.

These confirm that _detect_providers() is importable and callable on any
machine, returns a valid non-empty list, and always includes
CPUExecutionProvider as a fallback. The CPU-only case is exercised by
mocking the module-level onnxruntime alias (engine._ort) so no reload or
real accelerator is needed.
"""

from __future__ import annotations

from unittest.mock import patch

_VALID_EPS = {
    "CPUExecutionProvider",
    "CUDAExecutionProvider",
    "CoreMLExecutionProvider",
    "TensorrtExecutionProvider",
    "ROCMExecutionProvider",
    "OpenVINOExecutionProvider",
    "DnnlExecutionProvider",
}


def test_detect_providers_returns_non_empty_list() -> None:
    from axon.embedder.engine import _detect_providers

    result = _detect_providers()
    assert isinstance(result, list)
    assert len(result) >= 1, "provider list must not be empty"


def test_detect_providers_always_includes_cpu() -> None:
    from axon.embedder.engine import _detect_providers

    assert "CPUExecutionProvider" in _detect_providers()


def test_detect_providers_valid_ep_names() -> None:
    from axon.embedder.engine import _detect_providers

    for ep in _detect_providers():
        assert ep in _VALID_EPS, f"unexpected EP name: {ep!r}"


def test_detect_providers_cpu_only_machine() -> None:
    """On a CPU-only machine, _detect_providers must return CPU as the sole EP."""
    with patch(
        "axon.embedder.engine._ort.get_available_providers",
        return_value=["CPUExecutionProvider"],
    ):
        from axon.embedder.engine import _detect_providers

        assert _detect_providers() == ["CPUExecutionProvider"]


def test_detect_providers_cuda_wins_over_coreml() -> None:
    """When both CUDA and CoreML are available, CUDA wins (priority order)."""
    with patch(
        "axon.embedder.engine._ort.get_available_providers",
        return_value=[
            "CoreMLExecutionProvider",
            "CUDAExecutionProvider",
            "CPUExecutionProvider",
        ],
    ):
        from axon.embedder.engine import _detect_providers

        result = _detect_providers()
        assert result[0] == "CUDAExecutionProvider"
        assert "CPUExecutionProvider" in result
        assert "CoreMLExecutionProvider" not in result
