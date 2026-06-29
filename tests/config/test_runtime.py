from __future__ import annotations

import pytest

from axon.config.runtime import _resolve_vector_backend


def test_resolve_vector_backend_defaults_pgvector():
    assert _resolve_vector_backend({}) == "pgvector"


def test_resolve_vector_backend_rejects_qdrant(monkeypatch):
    monkeypatch.setenv("AXON_VECTOR_BACKEND", "qdrant")
    with pytest.raises(ValueError):
        _resolve_vector_backend({})
