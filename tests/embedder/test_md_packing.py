from axon.embedder.md_chunker import Section, pack_sections


def _sec(path, body):
    return Section(tuple(path), 1, tuple(body.splitlines()))


def test_small_siblings_merge_into_one_group():
    # two tiny sections under the same top-level heading -> one group
    secs = [_sec(["A"], "## a\nshort"), _sec(["A", "b"], "### b\nalso short")]
    groups = pack_sections(secs)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_does_not_merge_across_top_level_boundary():
    secs = [_sec(["A"], "# A\nshort"), _sec(["B"], "# B\nshort")]
    groups = pack_sections(secs)
    assert [len(g) for g in groups] == [1, 1]


def test_oversized_single_section_is_its_own_group():
    big = _sec(["A"], "# A\n" + ("word " * 4000))  # > MAX tokens
    groups = pack_sections([big])
    assert len(groups) == 1 and len(groups[0]) == 1
