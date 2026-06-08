"""The embedding-backed pairwise-similarity seam (dec-115).

Validates the cosine math against a fake embedder with known vectors — no model
download, no network.
"""

from __future__ import annotations

from axon.recall.supersession import make_embedding_similarity


class _FakeEmbedder:
    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table

    def embed_one(self, text: str) -> list[float]:
        return self._table[text]


def test_identical_vectors_are_maximally_similar() -> None:
    emb = _FakeEmbedder({"a": [1.0, 0.0], "b": [1.0, 0.0]})
    sim = make_embedding_similarity(emb)
    assert sim("a", "b") == 1.0


def test_orthogonal_vectors_are_dissimilar() -> None:
    emb = _FakeEmbedder({"a": [1.0, 0.0], "b": [0.0, 1.0]})
    sim = make_embedding_similarity(emb)
    assert sim("a", "b") == 0.0


def test_partial_overlap_is_between() -> None:
    emb = _FakeEmbedder({"a": [1.0, 1.0], "b": [1.0, 0.0]})
    sim = make_embedding_similarity(emb)
    assert 0.6 < sim("a", "b") < 0.8  # cos(45°) ≈ 0.707


def test_zero_vector_is_safe() -> None:
    emb = _FakeEmbedder({"a": [0.0, 0.0], "b": [1.0, 0.0]})
    sim = make_embedding_similarity(emb)
    assert sim("a", "b") == 0.0
