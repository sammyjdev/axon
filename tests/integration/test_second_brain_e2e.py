"""End-to-end second-brain proof (Fase 1): Obsidian notes → retrieval.

This is the integration that ties GLYPH + AXON together for the personal
"second brain": a Markdown vault is ingested via the GLYPH notes extractor
into AXON's SQLite ``SessionStore`` (the A2 importer), and then AXON's own
``GraphContextSource`` — the very source ``ask`` / ``get_context`` use —
retrieves that note content back. No code graph, no Ollama, no network:
fully offline with an injected fake LLM and a deterministic embedder.

If this passes, the claim "AXON recalls my Obsidian notes, not just code"
holds across the real ingest + real retrieval boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from axon.context.contracts import ContextPack
from axon.context.graph_source import GraphContextSource
from axon.obsidian import discovery
from axon.obsidian.importer import ingest_vault
from axon.store.session_store import SessionStore

# The retrieval target is GLYPH's enums/graph; skip cleanly if the lib is absent.
pytest.importorskip("glyph", reason="glyph-kg not installed")


class _FakeNotesLLM:
    """Injected notes extractor — returns fixed entities/relations, no network."""

    def extract(self, system: str, text: str):  # noqa: ANN202
        from glyph.extract.document.llm import Usage
        from glyph.extract.document.schema_notes import (
            NotesEntity,
            NotesExtractionResult,
            NotesRelation,
        )

        result = NotesExtractionResult(
            entities=[
                NotesEntity(name="Aerus Narrator", kind="project"),
                NotesEntity(name="hosted frontier model", kind="concept"),
                NotesEntity(name="local SLM", kind="concept"),
            ],
            relations=[
                NotesRelation(
                    subject="Aerus Narrator",
                    predicate="RELATES_TO",
                    object="hosted frontier model",
                ),
                NotesRelation(
                    subject="hosted frontier model",
                    predicate="MENTIONS",
                    object="local SLM",
                ),
            ],
        )
        return result, Usage(input_tokens=12, output_tokens=6)


class _FakeEmbedder:
    """Deterministic bag-of-chars embedder — no model download, stable anchoring."""

    _DIM = 32

    def embed(self, texts: Sequence[str]) -> list[Sequence[float]]:
        vectors: list[Sequence[float]] = []
        for text in texts:
            vec = [0.0] * self._DIM
            for ch in text.lower():
                vec[ord(ch) % self._DIM] += 1.0
            vectors.append(vec)
        return vectors


def _make_obsidian_vault(root: Path) -> Path:
    vault = root / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "decisions.md").write_text(
        "# Narrator decision\n\n"
        "Decided the Aerus Narrator should use a hosted frontier model with RAG "
        "instead of a local SLM after a blind A/B test.\n",
        encoding="utf-8",
    )
    return vault


@pytest.fixture(autouse=True)
def _clear_vault_cache() -> None:
    discovery.clear_cache()


@pytest.fixture
async def store(tmp_path: Path):
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


async def test_obsidian_notes_are_ingested_and_retrievable(
    store: SessionStore, tmp_path: Path
) -> None:
    vault = _make_obsidian_vault(tmp_path)

    # --- Fase 1, step 1: ingest the vault into the SQLite graph (A2) ---------
    n_nodes, n_edges, _n_vec = await ingest_vault(
        store, vault_path=vault, llm=_FakeNotesLLM(), index_vectors=False
    )
    assert n_nodes >= 1, "ingest wrote no nodes"
    assert n_edges >= 1, "ingest wrote no edges"

    # The note entities live in the same graph the retriever reads.
    node_ids = {n["id"] for n in await store.all_nodes()}
    assert "aerus narrator" in node_ids

    # --- Fase 1, step 2: retrieve them back through AXON's own source --------
    source = GraphContextSource(store, _FakeEmbedder(), hops=2, anchors=2)
    pack = await source.context("Aerus Narrator", token_budget=500)

    assert isinstance(pack, ContextPack)
    assert pack.mode == "graph"
    assert pack.segments, "retrieval returned no segments for an ingested note"
    # The ingested note entity (not any code symbol) surfaces in the context.
    assert "Aerus Narrator" in pack.text


async def test_empty_vault_graph_yields_empty_pack(
    store: SessionStore, tmp_path: Path
) -> None:
    # No ingest at all → the same retrieval path must degrade to an empty pack,
    # never raise, so `ask` stays safe on a brand-new brain.
    source = GraphContextSource(store, _FakeEmbedder())
    pack = await source.context("anything")
    assert pack.mode == "graph"
    assert pack.segments == ()
