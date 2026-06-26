from axon.embedder.md_chunker import split_text, MAX_TOKENS
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
