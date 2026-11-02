"""CTX-06 and CTX-08: whole items, reserved overflow line, no cutting."""

from axon.context.brief_budget import BRIEF_BUDGET, overflow_line, pack_items


def test_items_that_fit_omit_the_overflow_line():
    packed = pack_items(["alpha", "beta"], budget=BRIEF_BUDGET)

    assert packed == "alpha\nbeta\n"
    assert "more via axon_get_context" not in packed


def test_overflow_line_is_reserved_before_the_next_item():
    items = ["aaaa", "bbbb", "cccc"]
    reserved = "aaaa\n" + overflow_line(2) + "\n"
    budget = len(reserved.encode("utf-8"))

    packed = pack_items(items, budget)

    assert len(packed.encode("utf-8")) <= budget
    assert packed == reserved
    assert "bbbb" not in packed
    assert "cccc" not in packed


def test_an_item_larger_than_the_budget_is_counted_and_not_cut():
    item = "X" * 80
    budget = len((overflow_line(1) + "\n").encode("utf-8"))

    packed = pack_items([item], budget)

    assert packed == overflow_line(1) + "\n"
    assert "X" not in packed


def test_budget_counts_utf8_bytes():
    wide = "é" * 10  # 20 bytes; "a" * 10 is 10
    narrow = "a" * 10
    budget = len((narrow + "\n" + overflow_line(1) + "\n").encode("utf-8"))

    packed = pack_items([wide, "rest"], budget)

    assert wide not in packed
    assert packed.splitlines() == [overflow_line(2)]
    kept = pack_items([narrow], budget)
    assert kept == narrow + "\n"
    assert len(kept.encode("utf-8")) <= budget


def test_empty_input_stays_empty():
    assert pack_items([], budget=BRIEF_BUDGET) == ""
