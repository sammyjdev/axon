from __future__ import annotations

import pytest

from axon.embedder.engine import (
    FASTEMBED_MODEL_DIMS,
    EmbedderEngine,
    _default_model,
    default_embedding_dimension,
)


class TestFastembedModelDims:
    def test_known_small_model_dim(self) -> None:
        assert FASTEMBED_MODEL_DIMS["BAAI/bge-small-en-v1.5"] == 384

    def test_known_base_model_dim(self) -> None:
        assert FASTEMBED_MODEL_DIMS["BAAI/bge-base-en-v1.5"] == 768


class TestEmbedderEngineDimension:
    def test_small_model_dimension(self) -> None:
        engine = EmbedderEngine(model_name="BAAI/bge-small-en-v1.5")
        assert engine.dimension == 384

    def test_base_model_dimension(self) -> None:
        engine = EmbedderEngine(model_name="BAAI/bge-base-en-v1.5")
        assert engine.dimension == 768

    def test_unknown_model_raises_key_error(self) -> None:
        engine = EmbedderEngine(model_name="unknown/model-x")
        with pytest.raises(KeyError, match="unknown/model-x"):
            _ = engine.dimension

    def test_dimension_does_not_load_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Ensure accessing .dimension never triggers _ensure_model (no download).
        loaded: list[str] = []

        def _fake_ensure(self: EmbedderEngine):
            loaded.append(self.model_name)
            raise AssertionError("model should not be loaded for dimension lookup")

        monkeypatch.setattr(EmbedderEngine, "_ensure_model", _fake_ensure)
        engine = EmbedderEngine(model_name="BAAI/bge-small-en-v1.5")
        assert engine.dimension == 384
        assert loaded == []


class TestDefaultEmbeddingDimension:
    def test_returns_positive_int(self) -> None:
        dim = default_embedding_dimension()
        assert isinstance(dim, int)
        assert dim > 0

    def test_matches_default_model_dim(self) -> None:
        expected = FASTEMBED_MODEL_DIMS[_default_model()]
        assert default_embedding_dimension() == expected

    def test_does_not_instantiate_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Guard against any TextEmbedding construction during dimension lookup.
        def _explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("TextEmbedding must not be constructed at import/dim-lookup time")

        monkeypatch.setattr("axon.embedder.engine.TextEmbedding", _explode)
        dim = default_embedding_dimension()
        assert dim > 0
