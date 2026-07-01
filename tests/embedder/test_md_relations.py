"""Tests for relation context injection in the MD chunker."""

from axon.embedder.md_chunker import (
    _body_references,
    _frontmatter_relations,
    chunk_markdown,
)


# --- _frontmatter_relations ---

def test_inline_list():
    fm = "id: dec-001\nrelates_to: [dec-002, ADR-G6]"
    assert _frontmatter_relations(fm) == [("relates_to", "dec-002"), ("relates_to", "ADR-G6")]


def test_block_list():
    fm = "supersedes:\n  - dec-001\n  - dec-002"
    assert _frontmatter_relations(fm) == [("supersedes", "dec-001"), ("supersedes", "dec-002")]


def test_scalar_value():
    fm = "requires: PostgreSQL"
    assert _frontmatter_relations(fm) == [("requires", "PostgreSQL")]


def test_unknown_fields_ignored():
    fm = "id: dec-001\nstatus: accepted\ndate: 2026-06-30"
    assert _frontmatter_relations(fm) == []


def test_empty_frontmatter():
    assert _frontmatter_relations("") == []


# --- _body_references ---

def test_local_links_extracted():
    body = "See [ADR-G7](../decisions/dec-g7.md) and [dec-121](./dec-121.md)"
    refs = _body_references(body)
    assert refs == ["dec-g7", "dec-121"]


def test_http_links_excluded():
    body = "See [GitHub](https://github.com/foo) and [local](./file.md)"
    assert _body_references(body) == ["file"]


def test_anchor_links_excluded():
    body = "See [section](#heading) and [other](./file.md)"
    assert _body_references(body) == ["file"]


def test_fragment_in_path_stripped():
    body = "See [doc](./file.md#section)"
    assert _body_references(body) == ["file"]


def test_deduplicates_repeated_link():
    body = "See [a](./doc.md) and again [b](./doc.md)"
    assert _body_references(body) == ["doc"]


# --- chunk_markdown integration ---

def test_frontmatter_relations_in_first_chunk():
    src = "---\nrelates_to: [dec-001]\n---\n# Title\nsome content\n"
    chunks = chunk_markdown(src, "/vault/dec-002.md")
    assert "relates_to: dec-001" in chunks[0].content


def test_links_in_first_chunk():
    src = "# Title\nSee [ADR-G7](../decisions/dec-g7.md)\n"
    chunks = chunk_markdown(src, "/vault/doc.md")
    assert "references: dec-g7" in chunks[0].content


def test_frontmatter_and_links_combined():
    src = "---\nrelates_to: [dec-001]\n---\n# Title\nSee [doc2](./doc2.md)\n"
    chunks = chunk_markdown(src, "/vault/doc.md")
    assert "relates_to: dec-001" in chunks[0].content
    assert "references: doc2" in chunks[0].content


def test_no_injection_when_no_relations_or_links():
    src = "# Title\nsome plain content\n"
    chunks = chunk_markdown(src, "/vault/doc.md")
    assert "relates_to" not in chunks[0].content
    assert "references" not in chunks[0].content


def test_context_block_only_in_first_chunk():
    big_para = "word " * 200
    body = "\n\n".join(f"para {i}\n{big_para}" for i in range(8))
    src = f"---\nrelates_to: [dec-001]\n---\n# Title\n{body}\n"
    chunks = chunk_markdown(src, "/vault/doc.md")
    assert len(chunks) > 1
    assert "relates_to: dec-001" in chunks[0].content
    assert all("relates_to" not in c.content for c in chunks[1:])


def test_existing_frontmatter_strip_still_works():
    """Non-relation frontmatter fields must not appear in any chunk."""
    src = "---\nid: dec-001\nstatus: accepted\n---\n# My Title\nsome body\n"
    chunks = chunk_markdown(src, "/vault/dec-001.md")
    joined = "\n".join(c.content for c in chunks)
    assert "id: dec-001" not in joined
    assert "status: accepted" not in joined
    assert "---" not in joined
