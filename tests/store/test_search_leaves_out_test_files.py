"""A search leaves test files out unless it asks for them.

Measured 2026-10-15 on eight code questions: 41% of the hits the CLI returned
and 51% of the top 10 were test files, and the implementation a question was
about was often not in the top 10 at all.
"""

from __future__ import annotations

import os

import pytest

_PATHS = {
    "src/axon/embedder/engine.py": False,
    "docs/testing.md": False,
    "src/contest.py": False,
    "tests/embedder/test_engine.py": True,
    "test_utils.py": True,
    "pkg/store/foo_test.go": True,
    "web/src/app.spec.ts": True,
    "web/src/__tests__/app.js": True,
    "app/src/integrationTest/java/com/acme/FooIT.java": True,
    "app/src/test/java/com/acme/FooTest.java": True,
    "/Users/someone/dev/repo/tests/test_legacy.py": True,
}


def _chunk(file_path: str):
    from axon.store.vector_common import VECTOR_SIZE, Chunk

    return Chunk(
        id=file_path,
        vector=[0.1] * VECTOR_SIZE,
        file_path=file_path,
        language="python",
        chunk_type="function",
        symbol=file_path.rsplit("/", 1)[-1],
        project="proj",
        ctx="personal",
        content="def f(): pass",
    )


@pytest.fixture
async def store():
    from axon.store.pg_vector_store import PgVectorStore

    s = PgVectorStore(dsn=os.environ["AXON_PG_URL"])
    await s.ensure_collections()
    await s.upsert_batch([_chunk(path) for path in _PATHS])
    yield s
    await s.close()


async def _paths(store, **kwargs) -> set[str]:
    from axon.store.vector_common import VECTOR_SIZE

    hits = await store.search(
        query_vector=[0.1] * VECTOR_SIZE, collections=["personal"], top_k=20,
        max_nodes=20, max_tokens=10**9, **kwargs,
    )
    return {hit["payload"]["file_path"] for hit in hits}


async def test_a_search_leaves_test_files_out(store) -> None:
    assert await _paths(store) == {path for path, is_test in _PATHS.items() if not is_test}


async def test_a_search_that_asks_for_tests_gets_them(store) -> None:
    assert await _paths(store, include_tests=True) == set(_PATHS)
