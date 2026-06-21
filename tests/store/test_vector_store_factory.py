from __future__ import annotations


def test_default_is_qdrant(monkeypatch) -> None:
    monkeypatch.delenv("AXON_VECTOR_BACKEND", raising=False)
    from axon.store.vector_store import VectorStore
    from axon.store.vector_store_factory import make_vector_store
    assert isinstance(make_vector_store(), VectorStore)


def test_pgvector_selected_by_env(monkeypatch) -> None:
    monkeypatch.setenv("AXON_VECTOR_BACKEND", "pgvector")
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_store_factory import make_vector_store
    assert isinstance(make_vector_store(), PgVectorStore)
