from __future__ import annotations


def test_counts_match_exact() -> None:
    from scripts.verify_migration import counts_match

    assert counts_match(120, 120) is True
    assert counts_match(120, 119) is False


def test_parity_summary_reports_per_ctx() -> None:
    from scripts.verify_migration import parity_summary

    ok, text = parity_summary({"personal": (120, 120), "work": (5, 4)})
    assert ok is False
    assert "personal" in text and "work" in text
    assert "FAIL" in text
