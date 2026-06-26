from axon.embedder.md_chunker import parse_sections


def test_nested_headings_build_path():
    src = "# A\nintro\n## B\nbody\n### C\ndeep\n"
    secs = parse_sections(src)
    assert [s.heading_path for s in secs] == [("A",), ("A", "B"), ("A", "B", "C")]


def test_preamble_before_first_heading_is_its_own_section():
    src = "lead text\nmore\n# A\nbody\n"
    secs = parse_sections(src)
    assert secs[0].heading_path == ()
    assert secs[0].lines == ("lead text", "more")


def test_hash_inside_code_fence_is_not_a_heading():
    src = "# A\n```\n# not a heading\n```\ntail\n"
    secs = parse_sections(src)
    assert [s.heading_path for s in secs] == [("A",)]
    assert "# not a heading" in "\n".join(secs[0].lines)
