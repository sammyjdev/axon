from __future__ import annotations


def test_default_is_pgvector(monkeypatch) -> None:
    monkeypatch.delenv("AXON_VECTOR_BACKEND", raising=False)
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_store_factory import make_vector_store
    assert isinstance(make_vector_store(), PgVectorStore)


def test_pgvector_selected_by_env(monkeypatch) -> None:
    monkeypatch.setenv("AXON_VECTOR_BACKEND", "pgvector")
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_store_factory import make_vector_store
    assert isinstance(make_vector_store(), PgVectorStore)


def test_backend_from_runtime_vector_backend(monkeypatch) -> None:
    monkeypatch.delenv("AXON_VECTOR_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_store_factory import make_vector_store

    rt = load_runtime_config()
    # construct a runtime explicitly on pgvector (frozen dataclass -> use replace)
    import dataclasses

    rt_pg = dataclasses.replace(rt, vector_backend="pgvector")
    assert isinstance(make_vector_store(rt_pg), PgVectorStore)
