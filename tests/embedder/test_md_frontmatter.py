from axon.embedder.chunker import chunk_source
from axon.embedder.md_chunker import _strip_frontmatter


def test_strip_frontmatter_returns_body_and_offset():
    src = "---\na: 1\nb: two\n---\n# Head\nbody\n"
    body, offset = _strip_frontmatter(src)
    assert body == "# Head\nbody\n"
    assert offset == 4  # ---, a, b, --- consumed before the body


def test_no_frontmatter_is_unchanged():
    src = "# Head\nbody\n"
    assert _strip_frontmatter(src) == (src, 0)


def test_unterminated_frontmatter_is_treated_as_body():
    src = "---\na: 1\n# Head\nbody\n"  # no closing ---
    assert _strip_frontmatter(src) == (src, 0)


def test_frontmatter_is_not_embedded_and_line_numbers_shift():
    src = "---\nid: dec-001\nstatus: active\n---\n# My Title\nsome body text\n"
    chunks = chunk_source(src, "markdown", "/x/dec-001.md")
    joined = "\n".join(c.content for c in chunks)
    assert "id: dec-001" not in joined  # frontmatter not embedded
    assert "---" not in joined
    assert "My Title" in joined
    # "# My Title" is line 5 in the original (4 frontmatter lines precede it)
    assert chunks[0].start_line == 5


def test_middocument_thematic_break_is_not_stripped():
    src = "# Head\nbefore\n\n---\n\nafter\n"  # --- is a thematic break, not frontmatter
    body, offset = _strip_frontmatter(src)
    assert offset == 0 and body == src
