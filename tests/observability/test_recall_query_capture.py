# tests/observability/test_recall_query_capture.py
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _chunks_written(tmp_path: Path) -> list[dict]:
    # RuntimeConfig.data_root is engine_root / "data", and engine_root comes
    # from AXON_ENGINE (src/axon/config/runtime.py:148).
    chunks_file = tmp_path / "data" / "recall" / "chunks.jsonl"
    if not chunks_file.exists():
        return []
    return [
        json.loads(line)
        for line in chunks_file.read_text(encoding="utf-8").splitlines()
        if line
    ]


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    return tmp_path


def test_query_is_recorded_for_a_normal_ctx(data_root: Path) -> None:
    from axon.mcp import server

    server._record_chunk_recall(
        query="onde os vetores sao removidos",
        strategy_name="balanced",
        requested_max_tokens=4000,
        hits=[{"payload": {"content": "x", "file_path": "a.py"}, "score": 0.5}],
        ctx="knowledge",
    )
    rows = _chunks_written(data_root)
    assert rows and rows[0]["query"] == "onde os vetores sao removidos"


def test_query_is_never_recorded_for_work(data_root: Path) -> None:
    """dec-109/dec-131: work isolation must not leak through a telemetry file."""
    from axon.mcp import server

    server._record_chunk_recall(
        query="segredo do cliente avangrid",
        strategy_name="balanced",
        requested_max_tokens=4000,
        hits=[{"payload": {"content": "x", "file_path": "a.py"}, "score": 0.5}],
        ctx="work",
    )
    rows = _chunks_written(data_root)
    assert rows, "the record must still be written - only the query is withheld"
    assert rows[0]["query"] is None
    assert "avangrid" not in json.dumps(rows[0]).lower()


def test_query_hash_is_still_recorded_for_work(data_root: Path) -> None:
    """The hash carries no plaintext and keeps work rows countable."""
    from axon.mcp import server

    server._record_chunk_recall(
        query="segredo do cliente avangrid",
        strategy_name="balanced",
        requested_max_tokens=4000,
        hits=[{"payload": {"content": "x", "file_path": "a.py"}, "score": 0.5}],
        ctx="work",
    )
    rows = _chunks_written(data_root)
    assert rows[0]["query_hash"]
