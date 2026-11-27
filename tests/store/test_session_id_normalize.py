"""#187: what counts as a harness session id."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  abc ", "abc"),
        ("a" * 200, "a" * 200),
        ("", None),
        ("   ", None),
        (123, None),
        ({}, None),
        (None, None),  # absent from the payload
        ("a" * 201, None),
    ],
    ids=["padded", "200", "empty", "blank", "number", "object", "absent", "201"],
)
def test_normalize_session_id(raw, expected) -> None:
    from axon.store.session_id import normalize_session_id

    assert normalize_session_id(raw) == expected


@pytest.mark.parametrize("blank", ["", "   "])
def test_session_memory_normalizes_blank_session_id(blank) -> None:
    from axon.store.session_store import SessionMemory

    mem = SessionMemory(project="p", summary="s", raw_turns=1, session_id=blank)

    assert mem.session_id is None
    assert SessionMemory(project="p", summary="s", raw_turns=1).session_id is None
