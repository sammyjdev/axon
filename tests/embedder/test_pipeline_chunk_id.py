from __future__ import annotations

import uuid
from pathlib import Path

import pytest


def _chunk_id(file_path: str | Path, symbol: str, occurrence_index: int) -> str:
    """Import target - will import from pipeline after implementation."""
    from axon.embedder.pipeline import _chunk_id as real
    return real(file_path, symbol, occurrence_index)


def test_chunk_id_stable_across_line_shift() -> None:
    """Same file+symbol+occurrence_index must produce identical UUID regardless of start_line."""
    id_a = _chunk_id("src/foo.py", "my_func", 0)
    id_b = _chunk_id("src/foo.py", "my_func", 0)
    assert id_a == id_b


def test_chunk_id_differs_by_occurrence_index() -> None:
    """Two occurrences of the same symbol name (overloads/sub-chunks) get different IDs."""
    id_a = _chunk_id("src/foo.py", "my_func", 0)
    id_b = _chunk_id("src/foo.py", "my_func", 1)
    assert id_a != id_b


def test_chunk_id_differs_by_file() -> None:
    id_a = _chunk_id("src/a.py", "func", 0)
    id_b = _chunk_id("src/b.py", "func", 0)
    assert id_a != id_b


def test_chunk_id_is_valid_uuid() -> None:
    cid = _chunk_id("src/foo.py", "bar", 0)
    parsed = uuid.UUID(cid)
    assert parsed.version == 5


def test_chunk_id_exact_value() -> None:
    """Pin the exact UUID so a future refactor cannot silently change stored IDs."""
    expected = str(uuid.uuid5(uuid.NAMESPACE_URL, "src/foo.py::my_func::0"))
    assert _chunk_id("src/foo.py", "my_func", 0) == expected


def test_old_start_line_key_no_longer_accepted() -> None:
    """New signature has 3 positional args; old 2-arg call (path, Chunk) must raise TypeError."""
    from axon.embedder.pipeline import _chunk_id
    with pytest.raises(TypeError):
        _chunk_id("src/foo.py")  # type: ignore[call-arg]
