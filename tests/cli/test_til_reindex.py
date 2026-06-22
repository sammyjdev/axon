"""TIL → HOW-TO promotion must reindex the new HOW-TO automatically.

The vault watcher is a separate process that is not always running. To honour
"promote and find immediately", `_do_promote_today` reindexes each HOW-TO it
just produced. These tests pin that contract.
"""

from __future__ import annotations

from pathlib import Path

from axon.cli import pb as pb_module


class _FakeVectorStore:
    def __init__(self, *args, **kwargs):
        pass

    async def ensure_collections(self):
        return None

    async def close(self):
        return None


class _FakeEmbedder:
    def __init__(self, *args, **kwargs):
        pass


def test_promote_today_reindexes_each_howto(monkeypatch, tmp_path):
    howto_a = tmp_path / "howto-a.md"
    howto_b = tmp_path / "howto-b.md"

    def fake_run():
        return [howto_a, howto_b]

    calls: list[Path] = []

    async def fake_index_path(target, **kwargs):
        calls.append(target)
        return 1, 3  # (indexed_files, total_chunks)

    monkeypatch.setattr("axon.vault.til_promoter.run", fake_run)
    monkeypatch.setattr("axon.embedder.engine.EmbedderEngine", _FakeEmbedder)
    monkeypatch.setattr("axon.store.vector_store.VectorStore", _FakeVectorStore)
    monkeypatch.setattr(
        "axon.store.vector_store_factory.make_vector_store",
        lambda *a, **k: _FakeVectorStore(),
    )
    monkeypatch.setattr("axon.embedder.pipeline.index_path", fake_index_path)

    pb_module._do_promote_today()

    assert calls == [howto_a, howto_b]


def test_promote_today_no_tils_skips_index(monkeypatch):
    def fake_run():
        return []

    called = {"count": 0}

    async def fake_index_path(target, **kwargs):
        called["count"] += 1
        return 0, 0

    monkeypatch.setattr("axon.vault.til_promoter.run", fake_run)
    monkeypatch.setattr("axon.embedder.pipeline.index_path", fake_index_path)

    pb_module._do_promote_today()

    assert called["count"] == 0


def test_promote_today_survives_reindex_failure(monkeypatch, tmp_path):
    """Promotion must not crash if the index is offline — the HOW-TO file is
    the source of truth; reindex is best-effort."""
    howto = tmp_path / "howto-x.md"

    def fake_run():
        return [howto]

    async def boom(target, **kwargs):
        raise RuntimeError("qdrant offline")

    monkeypatch.setattr("axon.vault.til_promoter.run", fake_run)
    monkeypatch.setattr("axon.embedder.engine.EmbedderEngine", _FakeEmbedder)
    monkeypatch.setattr("axon.store.vector_store.VectorStore", _FakeVectorStore)
    monkeypatch.setattr(
        "axon.store.vector_store_factory.make_vector_store",
        lambda *a, **k: _FakeVectorStore(),
    )
    monkeypatch.setattr("axon.embedder.pipeline.index_path", boom)

    # Must not raise.
    pb_module._do_promote_today()
