from axon.embedder.md_chunker import MAX_TOKENS, _atoms, split_text
from axon.embedder.tokens import estimate_tokens


def test_small_text_is_single_window():
    assert split_text("one short paragraph") == ["one short paragraph"]


def test_large_text_splits_under_max_with_overlap():
    paras = "\n\n".join(f"para {i} " + "word " * 200 for i in range(6))
    windows = split_text(paras)
    assert len(windows) >= 2
    assert all(estimate_tokens(w) <= MAX_TOKENS for w in windows)
    # overlap: the tail of window 0 reappears at the head of window 1
    assert windows[0].split()[-1] in windows[1]


def test_table_block_is_not_split_midrow():
    table = "\n".join(f"| r{i} | v{i} |" for i in range(120))  # one big table
    windows = split_text(table)
    # the table stays whole (atomic) even though it exceeds MAX
    assert len(windows) == 1
    assert windows[0].count("\n") == 119


# --- Bug 1: overlap-carry sum bug ---
def test_overlap_carry_does_not_produce_over_budget_window():
    """
    Crafts atoms so that overlap_atom + next_atom together exceed max_tokens,
    but overlap_atom alone is <= max_tokens * _OVERLAP_CARRY_RATIO.
    The buggy guard only checks overlap_atom alone, so it carries and the combined
    window exceeds max_tokens. The fixed guard checks the combined size.

    _OVERLAP_CARRY_RATIO = 0.12 * 4 = 0.48, so threshold is 512 * 0.48 ~= 245 tokens.
    overlap_atom at ~230 tokens passes the old guard alone but combined with a
    ~310-token next_atom sums to ~540 > MAX_TOKENS=512.
    """
    # ~230 tokens: 230 / 0.35 ~= 657 chars
    overlap_candidate = "a" * 657
    # ~310 tokens: 310 / 0.35 ~= 886 chars
    next_atom_text = "b" * 886
    # Filler to fill up the first window so that overlap_candidate becomes the
    # last atom carried into the next window's overlap slot.
    # Each filler paragraph is ~100 tokens (100/0.35 ~= 286 chars).
    filler = "\n\n".join("f" * 286 for _ in range(5))
    text = filler + "\n\n" + overlap_candidate + "\n\n" + next_atom_text

    windows = split_text(text)
    assert len(windows) >= 2, "Should have split into multiple windows"
    for w in windows:
        lines = w.strip().splitlines()
        is_table = bool(lines) and all(ln.lstrip().startswith("|") for ln in lines if ln.strip())
        if not is_table:
            assert estimate_tokens(w) <= MAX_TOKENS, (
                f"Window exceeds MAX_TOKENS={MAX_TOKENS}: got {estimate_tokens(w)} tokens"
            )


# --- Bug 2: word-window step overshoot with long-token words ---
def test_atoms_long_token_words_stay_within_budget():
    """
    Long 'words' (many chars, like URLs or hashes) cause the fixed-step word splitter
    to overshoot the token budget. step = max(1, int(512/0.35/6)) ~= 243 words.
    At 30 chars/word, 243 words = 7290 chars = ~2551 tokens >> MAX_TOKENS.
    The fixed token-budget accumulation flushes before exceeding the cap.
    """
    long_word = "x" * 30  # 30 chars = ~10.5 tokens each
    # 500 such words: total ~5250 tokens >> MAX_TOKENS, forces word-window path.
    sentence = " ".join([long_word] * 500)
    atoms = _atoms(sentence, MAX_TOKENS)
    assert len(atoms) >= 2, "Should have split long-token sentence into multiple atoms"
    for atom in atoms:
        lines = atom.strip().splitlines()
        is_table = bool(lines) and all(ln.lstrip().startswith("|") for ln in lines if ln.strip())
        if not is_table:
            assert estimate_tokens(atom) <= MAX_TOKENS, (
                f"Atom exceeds MAX_TOKENS={MAX_TOKENS}: got {estimate_tokens(atom)} tokens"
            )
