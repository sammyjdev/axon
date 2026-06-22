from __future__ import annotations

from axon.embedder.chunker import _MAX_CHUNK_LINES, chunk_source


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
    def test_3_headers_yield_3_chunks(self) -> None:
        md = "# Intro\nsome text\n## Usage\ncommand\n### Details\nmore\n"
        chunks = chunk_source(md, "markdown", "README.md")
        section_chunks = [c for c in chunks if c.chunk_type == "section"]
        assert len(section_chunks) == 3
        # heading boundaries: each section starts at its heading line and the
        # heading line belongs to its own section.
        assert [(c.symbol, c.start_line, c.end_line) for c in section_chunks] == [
            ("Intro", 1, 2),
            ("Usage", 3, 4),
            ("Details", 5, 6),
        ]

    def test_section_chunk_type(self) -> None:
        md = "# Title\ncontent here\n"
        chunks = chunk_source(md, "markdown", "doc.md")
        assert all(c.chunk_type == "section" for c in chunks)

    def test_large_section_splits(self) -> None:
        body = "\n".join(f"paragraph {i}" for i in range(150))
        md = f"# Big Section\n{body}\n"
        chunks = chunk_source(md, "markdown", "big.md")
        for c in chunks:
            size = c.end_line - c.start_line + 1
            assert size <= _MAX_CHUNK_LINES, f"{c.symbol}: {size} lines"

    def test_no_headers_splits_on_line_cap(self) -> None:
        md = "\n".join(f"line {i}" for i in range(500))
        chunks = chunk_source(md, "markdown", "plain.md")
        assert len(chunks) == 7  # ceil(500/80) = 7 (6x80 + 1x20)
        for c in chunks:
            assert c.end_line - c.start_line + 1 <= _MAX_CHUNK_LINES
        # exact boundaries: contiguous 1-based ranges, all sub-chunks named plain[idx]
        assert chunks[0].symbol == "plain[0]"
        assert chunks[0].start_line == 1
        assert chunks[6].symbol == "plain[6]"
        assert chunks[6].end_line == 500

    def test_pre_header_content_is_chunked(self) -> None:
        md = "preamble text\n# Section\ncontent\n"
        chunks = chunk_source(md, "markdown", "doc.md")
        assert len(chunks) == 2
        # preamble is its own one-line chunk; the section starts at the heading.
        assert chunks[0].start_line == 1 and chunks[0].end_line == 1
        assert chunks[1].symbol == "Section"
        assert chunks[1].start_line == 2


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
