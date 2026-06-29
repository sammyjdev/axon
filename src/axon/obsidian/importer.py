"""Obsidian vault ingest (A2).

Walks an Obsidian vault for ``.md`` notes, extracts a knowledge graph via
GLYPH's ``DocumentExtractor(domain="notes")``, and writes the resulting
nodes/edges into AXON's ``SessionStore`` so the existing
``GraphContextSource`` can retrieve them.

Edge-type mapping
-----------------
AXON's ``EdgeType`` is a closed Literal — no new values may be added here.
All GLYPH note predicates (RELATES_TO, MENTIONS, PART_OF, AUTHORED_BY,
DEPENDS_ON) are mapped to ``"touches"``, the neutral "X references Y" verb
that ``graph_source.py`` already collapses to GLYPH ``REFERENCES`` at query
time.  The original GLYPH predicate is preserved in
``Edge.payload["relation"]`` so downstream consumers can still distinguish
the original semantic.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from axon.core.edge import Edge
from axon.obsidian.discovery import discover_vault
from axon.store.session_store import SessionStore

logger = logging.getLogger(__name__)

# Stable namespace so a re-ingest upserts (not duplicates) the same note chunk.
_NOTE_NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")


def _is_atx_heading(line: str) -> bool:
    """True only for real ATX markdown headings (``#`` .. ``######`` + space).

    Guards against Obsidian ``#tag`` lines, which start with ``#`` but are not
    headings and must not split a note into a spurious chunk.
    """
    stripped = line.lstrip()
    level = len(stripped) - len(stripped.lstrip("#"))
    return 1 <= level <= 6 and stripped[level : level + 1] in ("", " ")


def _note_chunks(text: str) -> list[tuple[str, str]]:
    """Split a markdown note into ``(heading, body)`` chunks for vector indexing.

    Strips a leading YAML frontmatter block, then splits on markdown headings so
    each section (heading line included) is retrievable on its own. A note with
    no headings yields a single whole-file chunk.
    """
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]

    chunks: list[tuple[str, str]] = []
    heading = ""
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            chunks.append((heading, body))

    for line in text.splitlines():
        if _is_atx_heading(line):
            flush()
            heading = line.lstrip("# ").strip()
            buf = [line]
        else:
            buf.append(line)
    flush()

    if not chunks:
        body = text.strip()
        if body:
            chunks.append(("", body))
    return chunks


async def _index_note_vectors(
    md_files: list[Path],
    *,
    vault_name: str,
    ctx: str,
    vector_store: object,
    embedder: object | None,
) -> int:
    """Embed note chunks and upsert them into the ``ctx`` Qdrant collection.

    This is what makes ingested notes reachable by the primary retrieval path
    (``_retrieve_context`` → vector search), not only by direct graph queries.
    """
    items: list[tuple[Path, str, str]] = []
    for md_file in md_files:
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for heading, body in _note_chunks(text):
            items.append((md_file, heading, body))
    if not items:
        return 0

    if embedder is None:
        from axon.embedder.engine import EmbedderEngine

        embedder = EmbedderEngine()
    vectors = embedder.embed([body for _, _, body in items])  # type: ignore[attr-defined]

    from axon.store.vector_common import Chunk as VectorChunk

    chunks = [
        VectorChunk(
            id=str(uuid.uuid5(_NOTE_NS, f"{md_file}:{i}:{heading}")),
            vector=list(vec),
            file_path=str(md_file),
            language="markdown",
            chunk_type="note",
            symbol=heading or md_file.stem,
            project=vault_name,
            ctx=ctx,
            content=body,
        )
        for i, ((md_file, heading, body), vec) in enumerate(zip(items, vectors))
    ]

    await vector_store.ensure_collections()  # type: ignore[attr-defined]
    await vector_store.upsert_batch(chunks)  # type: ignore[attr-defined]
    return len(chunks)


async def ingest_vault(
    store: SessionStore,
    *,
    vault_path: Path | None = None,
    provider: str = "litellm",
    model: str = "ollama/llama3",
    base_url: str | None = "http://localhost:11434",
    api_key: str | None = None,
    llm: object | None = None,
    index_vectors: bool = True,
    ctx: str = "personal",
    vector_store: object | None = None,
    embedder: object | None = None,
) -> tuple[int, int, int]:
    """Extract a knowledge graph from an Obsidian vault and write it into *store*.

    Notes are written to TWO stores so both retrieval paths see them:
    the SQLite graph (entities/relations, via ``GraphContextSource``) AND the
    Qdrant ``ctx`` collection (note text, via the primary vector retrieval used
    by ``ask`` / ``search_code`` / the HTTP endpoint).

    Parameters
    ----------
    store:
        An already-``.init()``-ed ``SessionStore`` to write nodes/edges into.
    vault_path:
        Override the vault location.  Resolved via ``discover_vault()`` if
        not given; raises ``FileNotFoundError`` if no vault is found.
    provider, model, base_url, api_key:
        Forwarded to ``make_extractor(ExtractorConfig(...))`` to construct the
        LLM extractor.  Ignored when *llm* is provided.
    llm:
        Injectable LLM extractor (satisfies the ``LLMExtractor`` Protocol).
        Useful in tests — passing a fake bypasses all network calls.
    index_vectors:
        When True (default), also embed and upsert note chunks into the vector
        store.  Failures (e.g. Qdrant down) are logged and skipped — the graph
        ingestion still succeeds.
    ctx:
        Target context/collection for the note vectors (default ``"personal"``).
    vector_store, embedder:
        Injectable for tests; constructed from runtime config when omitted.

    Returns
    -------
    tuple[int, int, int]
        ``(nodes_ingested, edges_ingested, vectors_indexed)``.
    """
    from glyph.extract.document.extractor import DocumentExtractor
    from glyph.extract.document.llm import ExtractorConfig, make_extractor

    # ── resolve vault ────────────────────────────────────────────────────────
    if vault_path is None:
        vault_path = discover_vault()
    if vault_path is None:
        raise FileNotFoundError(
            "No Obsidian vault found. "
            "Set AXON_VAULT or configure vault_root in the runtime config."
        )

    # ── build extractor ──────────────────────────────────────────────────────
    if llm is None:
        cfg = ExtractorConfig(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            domain="notes",
        )
        llm = make_extractor(cfg)

    extractor = DocumentExtractor(llm=llm, domain="notes")  # type: ignore[arg-type]

    # ── walk vault and extract per-file ──────────────────────────────────────
    md_files = sorted(vault_path.rglob("*.md"))
    seen_nodes: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()
    nodes_total = 0
    edges_total = 0

    for md_file in md_files:
        try:
            nodes, edges, _ = extractor.extract_with_usage(md_file)
        except Exception:  # noqa: BLE001 — skip bad file, mirror build_vault behaviour
            logger.warning("ingest_vault: skipping %s (extraction failed)", md_file)
            continue

        for node in nodes:
            node_type = node.type.value  # NodeType is a StrEnum → e.g. "concept"
            await store.add_node(
                node.id,
                node_type,
                label=node.label,
                payload=dict(node.attrs) if node.attrs else None,
            )
            if node.id not in seen_nodes:
                seen_nodes.add(node.id)
                nodes_total += 1

        for glyph_edge in edges:
            # Map all GLYPH note relations to "touches" (the neutral AXON edge
            # type).  The original predicate is kept in payload so nothing is
            # lost; graph_source.py maps "touches" → GLYPH REFERENCES at query
            # time which is the correct retrieval behaviour for note relations.
            axon_edge = Edge(
                source_id=glyph_edge.src,
                target_id=glyph_edge.dst,
                type="touches",
                payload={"relation": glyph_edge.type.value},
            )
            await store.add_edge(axon_edge)
            key = (glyph_edge.src, glyph_edge.dst, glyph_edge.type.value)
            if key not in seen_edges:
                seen_edges.add(key)
                edges_total += 1

    # ── vector-index notes so the primary (vector) retrieval sees them ───────
    vectors_total = 0
    if index_vectors:
        owns_store = vector_store is None
        try:
            if vector_store is None:
                from axon.store.vector_store_factory import make_vector_store

                vector_store = make_vector_store()
            vectors_total = await _index_note_vectors(
                md_files,
                vault_name=vault_path.name,
                ctx=ctx,
                vector_store=vector_store,
                embedder=embedder,
            )
        except Exception as exc:  # noqa: BLE001 — vector store is best-effort
            logger.warning("ingest_vault: vector indexing skipped (%s)", exc)
        finally:
            if owns_store and vector_store is not None:
                close = getattr(vector_store, "close", None)
                if close is not None:
                    try:
                        await close()
                    except Exception:  # noqa: BLE001
                        pass

    logger.info(
        "ingest_vault: processed %d files → %d nodes, %d edges, %d vectors from %s",
        len(md_files),
        nodes_total,
        edges_total,
        vectors_total,
        vault_path,
    )
    return nodes_total, edges_total, vectors_total
