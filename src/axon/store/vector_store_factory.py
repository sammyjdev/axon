from __future__ import annotations


def make_vector_store(runtime=None):
    """Build the vector store. pgvector is the only backend (dec-121 Phase 1)."""
    from axon.config.runtime import load_runtime_config
    from axon.store.pg_vector_store import PgVectorStore

    rt = runtime or load_runtime_config()
    return PgVectorStore(dsn=rt.pg_url)
