from __future__ import annotations


def _hit(content: str, file_path: str, score: float = 0.5) -> dict:
    return {"score": score, "payload": {"content": content, "file_path": file_path}}


def test_identical_content_is_dropped() -> None:
    from axon.context.pack_dedup import dedup_hits

    out = dedup_hits([_hit("same", "a.py"), _hit("same", "b.py")])
    assert len(out) == 1


def test_at_most_two_chunks_per_file() -> None:
    """Two, not one: a large file can legitimately answer in more than one place."""
    from axon.context.pack_dedup import dedup_hits

    out = dedup_hits([_hit(f"c{i}", "a.py") for i in range(5)])
    assert len(out) == 2


def test_relative_order_is_preserved() -> None:
    from axon.context.pack_dedup import dedup_hits

    hits = [_hit("one", "a.py", 0.9), _hit("two", "b.py", 0.8), _hit("three", "c.py", 0.7)]
    assert [h["payload"]["content"] for h in dedup_hits(hits)] == ["one", "two", "three"]


def test_the_first_occurrence_wins() -> None:
    """Hits arrive ranked, so the kept copy must be the higher-ranked one."""
    from axon.context.pack_dedup import dedup_hits

    out = dedup_hits([_hit("same", "a.py", 0.9), _hit("same", "b.py", 0.1)])
    assert out[0]["score"] == 0.9


def test_empty_input_is_empty_output() -> None:
    from axon.context.pack_dedup import dedup_hits

    assert dedup_hits([]) == []


def test_missing_payload_does_not_crash() -> None:
    from axon.context.pack_dedup import dedup_hits

    assert len(dedup_hits([{"score": 0.5}, {"score": 0.4}])) == 1


def test_returns_same_list_identity_when_nothing_dropped() -> None:
    from axon.context.pack_dedup import dedup_hits

    hits = [_hit("content1", "a.py"), _hit("content2", "b.py")]
    out = dedup_hits(hits)
    assert out is hits


def test_returns_new_list_when_hits_are_dropped() -> None:
    from axon.context.pack_dedup import dedup_hits

    hits = [_hit("duplicate", "a.py"), _hit("duplicate", "b.py")]
    out = dedup_hits(hits)
    assert out is not hits
    assert len(out) == 1


def _hit_with_none_content(file_path: str, score: float = 0.5) -> dict:
    return {"score": score, "payload": {"content": None, "file_path": file_path}}


def test_none_content_behaves_like_missing_content() -> None:
    from axon.context.pack_dedup import dedup_hits

    none_hits = [_hit_with_none_content("a.py"), _hit_with_none_content("b.py")]
    missing_hits = [{"score": 0.5}, {"score": 0.4}]
    assert len(dedup_hits(none_hits)) == len(dedup_hits(missing_hits))

    text_none_and_none = [_hit("None", "a.py"), _hit_with_none_content("b.py")]
    assert len(dedup_hits(text_none_and_none)) == 2


