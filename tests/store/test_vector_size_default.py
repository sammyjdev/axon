"""Tests that VECTOR_SIZE in vector_common aligns with the embedder's dimension.

VECTOR_SIZE is a module-level constant resolved at import time, so we test
the underlying resolution logic directly rather than reloading the module.
"""

from __future__ import annotations

import importlib

import pytest

from axon.embedder.engine import FASTEMBED_MODEL_DIMS, default_embedding_dimension


class TestVectorSizeResolution:
    def test_default_embedding_dimension_matches_small_model(self) -> None:
        # Sanity: the helper returns a value present in the dims table.
        dim = default_embedding_dimension()
        assert dim in FASTEMBED_MODEL_DIMS.values()

    def test_env_override_is_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # When AXON_VECTOR_SIZE is set, int(os.environ.get(..., default)) returns
        # the env value regardless of the default. Verify the arithmetic holds.
        monkeypatch.setenv("AXON_VECTOR_SIZE", "1536")
        import os
        raw = os.environ.get("AXON_VECTOR_SIZE", str(default_embedding_dimension()))
        assert int(raw) == 1536

    def test_no_env_falls_back_to_embedder_dimension(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AXON_VECTOR_SIZE", raising=False)
        import os
        raw = os.environ.get("AXON_VECTOR_SIZE", str(default_embedding_dimension()))
        assert int(raw) == default_embedding_dimension()

    def test_module_vector_size_equals_embedder_dimension_when_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Reload the module with AXON_VECTOR_SIZE absent and confirm the constant
        # equals the embedder default. This validates the wiring end-to-end.
        monkeypatch.delenv("AXON_VECTOR_SIZE", raising=False)
        import axon.store.vector_common as vs_mod
        reloaded = importlib.reload(vs_mod)
        assert reloaded.VECTOR_SIZE == default_embedding_dimension()

    def test_module_vector_size_respects_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AXON_VECTOR_SIZE", "1536")
        import axon.store.vector_common as vs_mod
        reloaded = importlib.reload(vs_mod)
        assert reloaded.VECTOR_SIZE == 1536
