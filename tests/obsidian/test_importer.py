"""Tests for Obsidian vault ingest (A2).

Graph-path tests use a fake LLM extractor and a real (tmp) SessionStore with
``index_vectors=False`` so no network, Ollama or Qdrant is required. The vector
path is covered separately with an injected fake embedder + vector store.
``asyncio_mode = "auto"`` is configured globally in pyproject.toml.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axon.obsidian import discovery
from axon.obsidian.importer import ingest_vault
from axon.store.session_store import SessionStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_obsidian_vault(root: Path, notes: dict[str, str]) -> Path:
    """Create a minimal Obsidian vault with .obsidian/ and the given notes."""
    vault = root / "vault"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    for name, content in notes.items():
        target = vault / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return vault


async def _store(tmp_path: Path) -> SessionStore:
    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    return store


# ---------------------------------------------------------------------------
# Fakes — satisfy the contracts without any network call.
# ---------------------------------------------------------------------------


class _FakeNotesLLM:
    """Returns a fixed NotesExtractionResult for every chunk it receives."""

    def extract(self, system: str, text: str):  # noqa: ANN202
        from glyph.extract.document.llm import Usage
        from glyph.extract.document.schema_notes import (
            NotesEntity,
            NotesExtractionResult,
            NotesRelation,
        )

        result = NotesExtractionResult(
            entities=[
                NotesEntity(name="Alice", kind="person"),
                NotesEntity(name="Project X", kind="project"),
                NotesEntity(name="Key Concept", kind="concept"),
            ],
            relations=[
                NotesRelation(subject="Alice", predicate="RELATES_TO", object="Project X"),
                NotesRelation(subject="Project X", predicate="MENTIONS", object="Key Concept"),
            ],
        )
        return result, Usage(input_tokens=10, output_tokens=5)


class _ErrorLLM:
    """Always raises so we can test per-file failure resilience."""

    def extract(self, system: str, text: str):  # noqa: ANN202
        raise RuntimeError("intentional LLM failure")


class _FakeEmbedder:
    """Returns a fixed-length vector per text — no model download."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeVectorStore:
    """Records ensure_collections / upsert_batch calls — no Qdrant."""

    def __init__(self) -> None:
        self.ensured = False
        self.upserts: list[object] = []
        self.closed = False

    async def ensure_collections(self) -> None:
        self.ensured = True

    async def upsert_batch(self, chunks: list[object]) -> None:
        self.upserts.extend(chunks)

    async def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_vault_cache() -> None:
    discovery.clear_cache()


# ---------------------------------------------------------------------------
# Graph-path tests (index_vectors=False)
# ---------------------------------------------------------------------------


async def test_ingest_writes_nodes_to_store(tmp_path: Path) -> None:
    vault = _make_obsidian_vault(tmp_path, {"note.md": "# Hello\nAlice knows Project X.\n"})
    store = await _store(tmp_path)
    try:
        nodes_n, _edges_n, _vec = await ingest_vault(
            store, vault_path=vault, llm=_FakeNotesLLM(), index_vectors=False
        )
        node_ids = {n["id"] for n in await store.all_nodes()}
        assert "alice" in node_ids
        assert "project x" in node_ids
        assert nodes_n >= 1
    finally:
        await store.close()


async def test_ingest_writes_edges_to_store(tmp_path: Path) -> None:
    vault = _make_obsidian_vault(tmp_path, {"note.md": "# Note\nAlice and Project X.\n"})
    store = await _store(tmp_path)
    try:
        _n, edges_n, _vec = await ingest_vault(
            store, vault_path=vault, llm=_FakeNotesLLM(), index_vectors=False
        )
        edges = await store.all_edges()
        assert edges_n >= 1
        assert len(edges) >= 1
    finally:
        await store.close()


async def test_edges_are_stored_as_touches_type(tmp_path: Path) -> None:
    """All GLYPH note relations must be mapped to the AXON 'touches' edge type."""
    vault = _make_obsidian_vault(tmp_path, {"note.md": "# Note\nbody\n"})
    store = await _store(tmp_path)
    try:
        await ingest_vault(store, vault_path=vault, llm=_FakeNotesLLM(), index_vectors=False)
        for edge in await store.all_edges():
            assert edge.type == "touches", f"Expected 'touches', got {edge.type!r}"
    finally:
        await store.close()


async def test_original_relation_preserved_in_payload(tmp_path: Path) -> None:
    """The original GLYPH predicate must survive in Edge.payload['relation']."""
    vault = _make_obsidian_vault(tmp_path, {"note.md": "# Note\nbody\n"})
    store = await _store(tmp_path)
    try:
        await ingest_vault(store, vault_path=vault, llm=_FakeNotesLLM(), index_vectors=False)
        edges = await store.all_edges()
        assert edges, "no edges were ingested"
        relations = {e.payload["relation"] for e in edges if e.payload}
        assert "relates_to" in relations or "mentions" in relations
    finally:
        await store.close()


async def test_per_file_failure_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A file whose extraction raises must not abort the whole ingest run."""
    vault = _make_obsidian_vault(tmp_path, {"bad.md": "# Bad\nwill fail\n"})
    store = await _store(tmp_path)
    try:
        nodes_n, edges_n, _vec = await ingest_vault(
            store, vault_path=vault, llm=_ErrorLLM(), index_vectors=False
        )
        assert nodes_n == 0
        assert edges_n == 0
    finally:
        await store.close()


async def test_multi_file_vault_accumulates_results(tmp_path: Path) -> None:
    vault = _make_obsidian_vault(tmp_path, {"a.md": "# A\nbody a\n", "b.md": "# B\nbody b\n"})
    store = await _store(tmp_path)
    try:
        nodes_n, _edges_n, _vec = await ingest_vault(
            store, vault_path=vault, llm=_FakeNotesLLM(), index_vectors=False
        )
        assert nodes_n >= 1
        assert len(await store.all_nodes()) >= 1
    finally:
        await store.close()


async def test_nested_md_files_are_discovered(tmp_path: Path) -> None:
    vault = _make_obsidian_vault(tmp_path, {"sub/deep.md": "# Deep\nbody\n"})
    store = await _store(tmp_path)
    try:
        nodes_n, _e, _vec = await ingest_vault(
            store, vault_path=vault, llm=_FakeNotesLLM(), index_vectors=False
        )
        assert nodes_n >= 1
    finally:
        await store.close()


async def test_no_vault_raises_file_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When neither vault_path is given nor auto-discovered, raise FileNotFoundError."""
    monkeypatch.setenv("AXON_VAULT", str(tmp_path / "nonexistent"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")
    store = await _store(tmp_path)
    try:
        with pytest.raises(FileNotFoundError, match="No Obsidian vault found"):
            await ingest_vault(store, llm=_FakeNotesLLM(), index_vectors=False)
    finally:
        await store.close()


async def test_vault_path_kwarg_overrides_discovery(tmp_path: Path) -> None:
    """Explicitly passing vault_path must not require AXON_VAULT."""
    vault = _make_obsidian_vault(tmp_path, {"note.md": "# Note\nbody\n"})
    store = await _store(tmp_path)
    try:
        nodes_n, _e, _vec = await ingest_vault(
            store, vault_path=vault, llm=_FakeNotesLLM(), index_vectors=False
        )
        assert nodes_n >= 1
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Vector-path tests (injected fake embedder + vector store)
# ---------------------------------------------------------------------------


async def test_vectors_indexed_into_ctx_collection(tmp_path: Path) -> None:
    """Note text is embedded and upserted into the requested ctx collection."""
    vault = _make_obsidian_vault(
        tmp_path, {"a.md": "# Title\nAlice works on Project X.\n"}
    )
    store = await _store(tmp_path)
    vs = _FakeVectorStore()
    try:
        n_nodes, _n_edges, n_vec = await ingest_vault(
            store,
            vault_path=vault,
            llm=_FakeNotesLLM(),
            vector_store=vs,
            embedder=_FakeEmbedder(),
            ctx="personal",
        )
        assert n_vec >= 1
        assert vs.ensured, "ensure_collections must be called before upsert"
        assert vs.upserts, "expected at least one chunk upserted"
        chunk = vs.upserts[0]
        assert chunk.ctx == "personal"
        assert chunk.chunk_type == "note"
        assert chunk.language == "markdown"
        assert "Alice" in chunk.content or "Title" in chunk.content
        # graph path still ran too
        assert n_nodes >= 1
    finally:
        await store.close()


async def test_no_vectors_flag_skips_indexing(tmp_path: Path) -> None:
    """--no-vectors (index_vectors=False) writes the graph but touches no vectors."""
    vault = _make_obsidian_vault(tmp_path, {"a.md": "# T\nbody\n"})
    store = await _store(tmp_path)
    vs = _FakeVectorStore()
    try:
        _n, _e, n_vec = await ingest_vault(
            store,
            vault_path=vault,
            llm=_FakeNotesLLM(),
            index_vectors=False,
            vector_store=vs,
        )
        assert n_vec == 0
        assert not vs.upserts
        assert not vs.ensured
    finally:
        await store.close()


async def test_vector_indexing_failure_is_non_fatal(tmp_path: Path) -> None:
    """If the vector store errors, the graph ingestion still succeeds."""

    class _BoomVectorStore(_FakeVectorStore):
        async def ensure_collections(self) -> None:
            raise RuntimeError("qdrant down")

    vault = _make_obsidian_vault(tmp_path, {"a.md": "# T\nAlice and Project X.\n"})
    store = await _store(tmp_path)
    try:
        n_nodes, _e, n_vec = await ingest_vault(
            store,
            vault_path=vault,
            llm=_FakeNotesLLM(),
            vector_store=_BoomVectorStore(),
            embedder=_FakeEmbedder(),
        )
        assert n_nodes >= 1  # graph still written
        assert n_vec == 0  # vector indexing skipped, not raised
    finally:
        await store.close()
