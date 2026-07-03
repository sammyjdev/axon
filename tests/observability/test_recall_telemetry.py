"""RecallTelemetryStore: one JSONL record per chat-completions request."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from axon.observability.recall_telemetry import RecallRecord, RecallTelemetryStore


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
