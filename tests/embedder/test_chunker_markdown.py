from __future__ import annotations

from axon.embedder.chunker import _MAX_CHUNK_LINES, chunk_source
from axon.embedder.md_chunker import MAX_TOKENS, MIN_TOKENS, _is_table_block
from axon.embedder.tokens import estimate_tokens


class TestSplitLinesIntoChunks:
    def test_200_lines_yields_3_chunks(self) -> None:
        from axon.embedder.chunker import _split_lines_into_chunks
        lines = [f"line {i}" for i in range(200)]
        chunks = _split_lines_into_chunks(lines, 1, "symbol", "function", "f.py", "python")
        assert len(chunks) == 3  # ceil(200/80) = 3 (80+80+40)

    def test_start_and_end_lines_correct(self) -> None:
        from axon.embedder.chunker import _split_lines_into_chunks
        lines = [f"line {i}" for i in range(200)]
        chunks = _split_lines_into_chunks(lines, 1, "sym", "function", "f.py", "python")
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 80
        assert chunks[1].start_line == 81
        assert chunks[1].end_line == 160
        assert chunks[2].start_line == 161
        assert chunks[2].end_line == 200

    def test_symbol_names_include_index(self) -> None:
        from axon.embedder.chunker import _split_lines_into_chunks
        lines = [f"line {i}" for i in range(200)]
        chunks = _split_lines_into_chunks(lines, 1, "sym", "function", "f.py", "python")
        assert chunks[0].symbol == "sym[0]"
        assert chunks[1].symbol == "sym[1]"
        assert chunks[2].symbol == "sym[2]"

    def test_80_lines_is_single_chunk(self) -> None:
        from axon.embedder.chunker import _split_lines_into_chunks
        lines = [f"line {i}" for i in range(80)]
        chunks = _split_lines_into_chunks(lines, 1, "s", "function", "f.py", "python")
        assert len(chunks) == 1
        assert chunks[0].symbol == "s[0]"


class TestChunkTypeSection:
    def test_chunk_type_section_valid(self) -> None:
        from axon.embedder.chunker import Chunk
        c = Chunk(
            symbol="intro",
            chunk_type="section",
            start_line=1,
            end_line=5,
            content="# Hello\nworld\n",
            file_path="README.md",
            language="markdown",
        )
        assert c.chunk_type == "section"


class TestMarkdownChunker:
    def test_3_headers_under_one_top_yields_1_packed_chunk(self) -> None:
        # All 3 headings share the same top-level heading ("Intro"), so
        # pack_sections merges them into a single group (total tokens << MAX_TOKENS).
        # The breadcrumb uses the LAST (deepest) section's heading_path, making the
        # symbol specific and unambiguous even when multiple sections are packed.
        md = "# Intro\nsome text\n## Usage\ncommand\n### Details\nmore\n"
        chunks = chunk_source(md, "markdown", "README.md")
        section_chunks = [c for c in chunks if c.chunk_type == "section"]
        assert len(section_chunks) == 1
        assert section_chunks[0].symbol == "README > Intro > Usage > Details"
        assert section_chunks[0].start_line == 1

    def test_section_chunk_type(self) -> None:
        md = "# Title\ncontent here\n"
        chunks = chunk_source(md, "markdown", "doc.md")
        assert all(c.chunk_type == "section" for c in chunks)

    def test_large_section_splits(self) -> None:
        # The new chunker splits by token budget (MAX_TOKENS=512), not line count.
        # Use double-newline paragraphs so split_text can split at paragraph boundaries.
        # Content includes a breadcrumb prefix (a few tokens); window body stays <= MAX_TOKENS.
        body = "\n\n".join(f"Sentence in paragraph {i}." for i in range(80))
        md = f"# Big Section\n{body}\n"
        chunks = chunk_source(md, "markdown", "big.md")
        assert len(chunks) >= 2  # too big to fit in one token window
        for c in chunks:
            # Strip the breadcrumb prefix to check just the window body stays in budget.
            # The breadcrumb is everything before the first "\n\n".
            body_part = c.content.split("\n\n", 1)[1] if "\n\n" in c.content else c.content
            assert estimate_tokens(body_part) <= MAX_TOKENS, f"{c.symbol}: {estimate_tokens(body_part)} tokens"

    def test_no_headers_splits_on_token_cap(self) -> None:
        # The new chunker splits by token budget (MAX_TOKENS=512), not line count.
        # 500 short lines produce overlapping token windows; all within the cap.
        md = "\n".join(f"line {i}" for i in range(500))
        chunks = chunk_source(md, "markdown", "plain.md")
        assert len(chunks) >= 2  # too much text to fit in one token window
        for c in chunks:
            assert estimate_tokens(c.content) <= MAX_TOKENS, f"{c.symbol}: {estimate_tokens(c.content)} tokens"
        # sub-chunks are named with [idx] suffix since there are multiple windows
        assert chunks[0].symbol == "plain[0]"
        assert chunks[-1].symbol == f"plain[{len(chunks) - 1}]"

    def test_pre_header_content_is_chunked(self) -> None:
        # Preamble (before first heading) becomes a section with empty heading_path.
        # Its breadcrumb is just the stem; the named section has the full breadcrumb.
        md = "preamble text\n# Section\ncontent\n"
        chunks = chunk_source(md, "markdown", "doc.md")
        assert len(chunks) == 2
        # preamble is its own chunk; the section starts at the heading.
        assert chunks[0].symbol == "doc"
        assert chunks[0].start_line == 1
        assert chunks[1].symbol == "doc > Section"
        assert chunks[1].start_line == 2


class TestBreadcrumbBudget:
    """The full chunk content (breadcrumb + body) must never exceed MAX_TOKENS."""

    def test_deep_heading_full_content_within_cap(self) -> None:
        # Deep heading path produces a long breadcrumb that eats into the token budget.
        # Bug: split_text sized windows to MAX_TOKENS ignoring breadcrumb overhead,
        # so full content = crumb + "\n\n" + window exceeded MAX_TOKENS.
        long_body = "\n\n".join(
            f"This is a prose paragraph number {i} with enough words to contribute to the token budget."
            for i in range(60)
        )
        md = f"# Level1\n## Level2\n### Level3\n#### Level4\n{long_body}\n"
        chunks = chunk_source(md, "markdown", "deep.md")
        over_cap = [
            c for c in chunks
            if not _is_table_block(c.content.split("\n\n", 1)[-1] if "\n\n" in c.content else c.content)
            and estimate_tokens(c.content) > MAX_TOKENS
        ]
        assert over_cap == [], (
            f"{len(over_cap)} chunk(s) exceeded MAX_TOKENS={MAX_TOKENS}: "
            + ", ".join(f"{c.symbol}={estimate_tokens(c.content)}" for c in over_cap)
        )

    def test_atomic_table_may_exceed_cap(self) -> None:
        # A pure table block exceeding the token budget must remain whole (atomic exception).
        # After the fix, split_text accepts a max_tokens budget; even so, tables bypass it.
        from axon.embedder.md_chunker import split_text
        wide_row = "| " + " | ".join(f"col{i}" for i in range(40)) + " |"
        sep_row = "| " + " | ".join("---" for _ in range(40)) + " |"
        data_rows = "\n".join(
            "| " + " | ".join(f"val{j}" for j in range(40)) + " |"
            for _ in range(5)
        )
        table = f"{wide_row}\n{sep_row}\n{data_rows}"
        assert _is_table_block(table), "Precondition: block must be a table"
        assert estimate_tokens(table) > MAX_TOKENS, "Precondition: table must exceed MAX_TOKENS"
        # Even with a very tight budget, the table stays as a single window
        windows = split_text(table, max_tokens=MIN_TOKENS)
        assert len(windows) == 1, f"Table was split into {len(windows)} windows; must stay atomic"
        assert _is_table_block(windows[0]), "Window must still be a table"
        assert estimate_tokens(windows[0]) > MAX_TOKENS, "Table window may exceed cap (atomic exception)"


class TestTextCatchall:
    def test_txt_large_file_splits(self) -> None:
        txt = "\n".join(f"line {i}" for i in range(160))
        chunks = chunk_source(txt, "text", "notes.txt")
        assert len(chunks) == 2
        for c in chunks:
            assert c.end_line - c.start_line + 1 <= _MAX_CHUNK_LINES

    def test_unknown_language_splits(self) -> None:
        content = "\n".join(f"row {i}" for i in range(200))
        chunks = chunk_source(content, "unknown_lang", "data.xyz")
        assert len(chunks) >= 3


class TestMarkdownChunkerBreadcrumb:
    def test_breadcrumb_prepended_to_content_and_symbol(self) -> None:
        src = "# Doc\n## Costs\n### Token cost\nbody text here\n"
        chunks = chunk_source(src, "markdown", "/x/ENGINE.md")
        c = chunks[-1]
        assert c.symbol == "ENGINE > Doc > Costs > Token cost"
        assert c.content.startswith("ENGINE > Doc > Costs > Token cost\n\n")

    def test_duplicate_headings_become_distinct_chunks(self) -> None:
        src = "# A\n## Token cost\nx\n# B\n## Token cost\ny\n"
        chunks = chunk_source(src, "markdown", "/x/d.md")
        symbols = [c.symbol for c in chunks]
        assert "d > A > Token cost" in symbols
        assert "d > B > Token cost" in symbols

    def test_no_chunk_below_min_except_singletons(self) -> None:
        src = "# A\n## a\nshort\n## b\nshort\n## c\nshort\n"
        chunks = chunk_source(src, "markdown", "/x/d.md")
        big = [c for c in chunks if estimate_tokens(c.content) >= MAX_TOKENS]
        assert big == []  # nothing over the cap
        assert len(chunks) >= 1

    def test_file_with_no_headings_falls_back(self) -> None:
        chunks = chunk_source("just prose, no headings", "markdown", "/x/note.md")
        assert len(chunks) == 1
        assert chunks[0].symbol == "note"


class TestSiblingBreadcrumb:
    """Bug 3: when siblings are packed together, breadcrumb should be the common prefix."""

    def test_sibling_sections_use_common_prefix_breadcrumb(self) -> None:
        # ## Alpha and ## Beta are siblings under # Top.
        # Both are small enough to be packed into one group.
        # Bug: breadcrumb uses group[-1].heading_path = ("Top", "Beta"),
        # so symbol is "stem > Top > Beta" instead of the correct "stem > Top".
        md = "# Top\n## Alpha\nsmall alpha body\n## Beta\nsmall beta body\n"
        chunks = chunk_source(md, "markdown", "/x/stem.md")
        # Both sibling sections pack into one chunk (combined tokens << MAX_TOKENS).
        # The common prefix of ("Top", "Alpha") and ("Top", "Beta") is ("Top",).
        section_chunks = [c for c in chunks if c.chunk_type == "section"]
        assert len(section_chunks) == 1, (
            f"Expected siblings packed into 1 chunk, got {len(section_chunks)}: "
            + str([c.symbol for c in section_chunks])
        )
        assert section_chunks[0].symbol == "stem > Top", (
            f"Expected common-prefix breadcrumb 'stem > Top', got '{section_chunks[0].symbol}'"
        )

    def test_nested_descending_chain_still_uses_deepest_path(self) -> None:
        # Pure descending chain: # Top > ## Sub > ### Deep.
        # heading_paths: ("Top",), ("Top","Sub"), ("Top","Sub","Deep")
        # Each is a prefix of the last, so the current behavior (use group[-1]) is correct.
        md = "# Top\nsome text\n## Sub\nmore\n### Deep\ndeepest\n"
        chunks = chunk_source(md, "markdown", "/x/doc.md")
        section_chunks = [c for c in chunks if c.chunk_type == "section"]
        assert len(section_chunks) == 1
        assert section_chunks[0].symbol == "doc > Top > Sub > Deep"
