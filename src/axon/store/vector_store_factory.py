from __future__ import annotations

import os


def make_vector_store(runtime=None):
    """Select the vector backend by AXON_VECTOR_BACKEND (default 'qdrant')."""
    from axon.config.runtime import load_runtime_config

    rt = runtime or load_runtime_config()
    backend = os.environ.get("AXON_VECTOR_BACKEND", "qdrant").strip().lower()
    if backend == "pgvector":
        from axon.store.pg_vector_store import PgVectorStore

        return PgVectorStore(dsn=rt.pg_url)
    from axon.store.vector_store import VectorStore

    return VectorStore(url=rt.qdrant_url)
