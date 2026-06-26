from axon.embedder.md_chunker import MAX_TOKENS, TARGET_TOKENS, Section, pack_sections
from axon.embedder.tokens import estimate_tokens


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


def test_siblings_between_target_and_max_do_not_merge():
    """Two same-top siblings whose combined tokens fall between TARGET (480) and MAX (512)
    must NOT merge — packing aims for TARGET, not MAX.

    Each section body is sized so that each alone is ~247 tokens; combined ~495 tokens,
    which satisfies TARGET < combined <= MAX (old behaviour: merged; new: separate).
    """
    # ~247 tokens: 247 / 0.35 ~= 706 chars of body content
    body_chars = int(247 / 0.35)
    body_a = "x " * (body_chars // 2)
    body_b = "y " * (body_chars // 2)
    sec_a = _sec(["A"], f"## A\n{body_a}")
    sec_b = _sec(["A", "B"], f"### B\n{body_b}")

    tok_a = estimate_tokens("\n".join(sec_a.lines))
    tok_b = estimate_tokens("\n".join(sec_b.lines))
    combined = tok_a + tok_b

    # Guard: ensure our sizes hit the intended window.
    assert TARGET_TOKENS < combined <= MAX_TOKENS, (
        f"Test setup error: combined={combined} must be in ({TARGET_TOKENS}, {MAX_TOKENS}]"
    )

    groups = pack_sections([sec_a, sec_b])
    assert len(groups) == 2, (
        f"Expected 2 groups (no merge above TARGET={TARGET_TOKENS}), "
        f"got {len(groups)}; combined tokens={combined}"
    )
