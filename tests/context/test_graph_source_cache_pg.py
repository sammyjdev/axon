"""dec-121 Phase 2: GLYPH graph cache must invalidate on Postgres state.

The cache used to key on the SQLite WAL mtime, which never changes under the
Postgres graph backend -> a re-index would serve a stale graph forever. These
tests pin the backend-correct invalidation signal.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.context.graph_source import GraphContextSource, get_cache_stats  # noqa: E402
from axon.store.session_store import SessionStore  # noqa: E402

try:  # graph_source hard-depends on glyph; skip cleanly if absent
    import glyph  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip("glyph-kg not installed", allow_module_level=True)


class FakeEmbedder:
    _DIM = 32

    def embed(self, texts: Sequence[str]) -> list[Sequence[float]]:
        vectors: list[Sequence[float]] = []
        for text in texts:
            vec = [0.0] * self._DIM
            for ch in text.lower():
                vec[ord(ch) % self._DIM] += 1.0
            vectors.append(vec)
        return vectors


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def pg_store(pg_dsn, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AXON_GRAPH_BACKEND", "postgres")
    monkeypatch.setenv("AXON_PG_URL", pg_dsn)
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


async def test_cache_invalidates_after_pg_write(pg_store: SessionStore):
    await pg_store.add_node("A", "symbol", label="A")
    source = GraphContextSource(pg_store, FakeEmbedder())

    before = get_cache_stats()
    await source.context("A")
    after_first = get_cache_stats()
    assert after_first["misses"] == before["misses"] + 1, "first call must be a miss"

    # A pure Postgres write does NOT touch the SQLite WAL mtime, so the old
    # mtime-keyed cache would wrongly serve a stale graph on the next read.
    await pg_store.add_node("B", "symbol", label="B")

    await source.context("A")
    after_second = get_cache_stats()
    assert after_second["misses"] == after_first["misses"] + 1, (
        "a Postgres graph write must invalidate the cache"
    )


async def test_cache_hits_when_pg_graph_unchanged(pg_store: SessionStore):
    await pg_store.add_node("Z", "symbol", label="Z")
    source = GraphContextSource(pg_store, FakeEmbedder())

    await source.context("Z")
    after_first = get_cache_stats()
    # No write between calls -> the signal is stable -> second call is a hit.
    await source.context("Z")
    after_second = get_cache_stats()
    assert after_second["hits"] == after_first["hits"] + 1
