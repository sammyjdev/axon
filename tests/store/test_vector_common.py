from axon.store.vector_common import VECTOR_SIZE, _rank_and_limit


def test_vector_size_is_a_positive_int():
    assert isinstance(VECTOR_SIZE, int) and VECTOR_SIZE > 0


def test_rank_and_limit_is_importable_and_callable():
    assert callable(_rank_and_limit)
