from axon.store.vector_common import VECTOR_SIZE, _rank_and_limit, transcript_covers


def test_vector_size_is_a_positive_int():
    assert isinstance(VECTOR_SIZE, int) and VECTOR_SIZE > 0


def test_rank_and_limit_is_importable_and_callable():
    assert callable(_rank_and_limit)


def test_transcript_covers_identical_text():
    text = "alpha " * 20

    assert transcript_covers(text, [text]) is True


def test_transcript_covers_disjoint_text():
    assert transcript_covers("alpha " * 20, ["beta " * 20]) is False


def test_transcript_covers_partial_overlap_straddles_cutoff():
    blocks = ["A" * 20, "B" * 20, "C" * 20, "D" * 20, "E" * 20, "F" * 20]
    chunk = "".join(blocks)

    assert transcript_covers(chunk, ["".join(blocks[:4])]) is True
    assert transcript_covers(chunk, ["".join(blocks[:3])]) is False


def test_transcript_covers_empty_transcript_is_false():
    assert transcript_covers("alpha " * 20, []) is False
