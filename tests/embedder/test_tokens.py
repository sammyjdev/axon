from axon.embedder.tokens import estimate_tokens


def test_estimate_tokens_is_035_per_char():
    assert estimate_tokens("a" * 100) == 35


def test_estimate_tokens_minimum_one():
    assert estimate_tokens("") == 1
