"""RecallTelemetryStore: one JSONL record per chat-completions request."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from axon.observability.recall_telemetry import (
    ChunkRecord,
    RecallRecord,
    RecallTelemetryStore,
)
from scripts.recall_usage_report import aggregate_usage


def _make_store(tmp_path: Path) -> RecallTelemetryStore:
    runtime = SimpleNamespace(data_root=tmp_path)
    return RecallTelemetryStore(runtime=runtime)  # type: ignore[arg-type]


def _record(**overrides) -> RecallRecord:
    base = dict(
        ts="2026-07-02T00:00:00+00:00",
        caller="http",
        include_context=True,
        model="ollama/qwen2.5:7b",
        prompt_tokens=512,
        completion_tokens=64,
        total_tokens=576,
        usage_source="provider",
    )
    base.update(overrides)
    return RecallRecord(**base)


def test_append_then_load_roundtrip(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.append(_record())
    store.append(_record(include_context=False, prompt_tokens=40, total_tokens=104))

    records = store.load_all()

    assert len(records) == 2
    assert records[0].prompt_tokens == 512
    assert records[0].usage_source == "provider"
    assert records[1].include_context is False


def test_load_all_empty_when_file_missing(tmp_path: Path) -> None:
    assert _make_store(tmp_path).load_all() == []


def test_stats_file_lives_under_recall_dir(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.stats_file == tmp_path / "recall" / "requests.jsonl"


def test_append_chunk_record_roundtrip(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    record = ChunkRecord(
        ts="2026-07-02T00:00:00+00:00",
        query_hash="f00d",
        strategy="balanced",
        requested_max_tokens=2000,
        chunks=[
            {
                "hash": "c0ffee",
                "score": 0.612,
                "ranking_score": 0.598,
                "token_estimate": 312,
                "file_path": "/tmp/a.py",
            },
            {
                "hash": "bada55",
                "score": 0.5,
                "ranking_score": None,
                "token_estimate": 1,
                "file_path": "/tmp/b.py",
            },
        ],
    )

    store.append_chunks(record)

    lines = (tmp_path / "recall" / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed == record.model_dump()


def _chunk_record(ts: str, query_hash: str, *chunks: dict) -> ChunkRecord:
    return ChunkRecord(
        ts=ts,
        query_hash=query_hash,
        strategy="balanced",
        requested_max_tokens=2000,
        chunks=list(chunks),
    )


def test_load_chunks_roundtrip_and_missing_file(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.load_chunks() == []

    first = _chunk_record(
        "2026-07-02T00:00:00+00:00",
        "q1",
        {"file_path": "/tmp/a.py", "ranking_score": 0.8},
    )
    second = _chunk_record(
        "2026-07-03T00:00:00+00:00",
        "q2",
        {"file_path": "/tmp/b.py", "ranking_score": None},
    )
    store.append_chunks(first)
    store.append_chunks(second)

    records = store.load_chunks()

    assert records == [first, second]
    assert records[0].chunks[0]["ranking_score"] == 0.8


def test_aggregate_usage_counts_queries_scores_since_and_unknowns() -> None:
    records = [
        _chunk_record(
            "2026-07-01T00:00:00+00:00",
            "old",
            {"file_path": "/tmp/old.py", "ranking_score": 1.0},
        ),
        _chunk_record(
            "2026-07-02T00:00:00+00:00",
            "q1",
            {"file_path": "/tmp/a.py", "ranking_score": 0.8},
            {"file_path": "/tmp/a.py", "ranking_score": None},
            {"ranking_score": 0.4},
        ),
        _chunk_record(
            "2026-07-03T00:00:00+00:00",
            "q1",
            {"file_path": "/tmp/a.py", "ranking_score": 0.6},
            {"file_path": "", "ranking_score": 0.2},
        ),
    ]

    rows = aggregate_usage(
        records,
        top=10,
        since="2026-07-02T00:00:00+00:00",
    )

    assert rows[0].file_path == "/tmp/a.py"
    assert rows[0].count == 3
    assert rows[0].distinct_queries == 1
    assert rows[0].mean_rank == pytest.approx(0.7)
    assert rows[1].file_path == "(unknown)"
    assert rows[1].count == 2
    assert rows[1].distinct_queries == 1
    assert rows[1].mean_rank == pytest.approx(0.3)
    assert len(rows) == 2


def test_chunk_record_old_line_without_file_path_still_parses() -> None:
    parsed = ChunkRecord.model_validate(
        {
            "ts": "2026-07-02T00:00:00+00:00",
            "query_hash": "f00d",
            "strategy": "balanced",
            "requested_max_tokens": 2000,
            "chunks": [
                {
                    "hash": "c0ffee",
                    "score": 0.612,
                    "ranking_score": 0.598,
                    "token_estimate": 312,
                }
            ],
        }
    )

    assert parsed.chunks[0]["hash"] == "c0ffee"
    assert parsed.chunks[0].get("file_path") is None
