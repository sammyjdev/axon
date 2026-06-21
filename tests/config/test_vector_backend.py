from __future__ import annotations

import pytest


def test_vector_backend_defaults_to_qdrant(monkeypatch) -> None:
    monkeypatch.delenv("AXON_VECTOR_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().vector_backend == "qdrant"


def test_vector_backend_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AXON_VECTOR_BACKEND", "pgvector")
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().vector_backend == "pgvector"


def test_vector_backend_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("AXON_VECTOR_BACKEND", "weaviate")
    from axon.config.runtime import load_runtime_config

    with pytest.raises(ValueError):
        load_runtime_config()
