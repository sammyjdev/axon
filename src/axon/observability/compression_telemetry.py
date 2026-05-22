from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from axon.config.runtime import RuntimeConfig, load_runtime_config


class CompressionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    ts: str
    engine: str  # e.g. "caveman/phi3+rtk", "caveman/phi3", "fallback"
    caller: str  # "claude-code", "codex", "cli"
    ctx: str | None
    before_tokens: int
    after_tokens: int
    reduction_tokens: int
    reduction_pct: float


class CompressionTelemetryStore:
    def __init__(self, runtime: RuntimeConfig | None = None) -> None:
        self._runtime = runtime or load_runtime_config()
        self._file = self._runtime.data_root / "compression" / "stats.jsonl"

    @property
    def stats_file(self) -> Path:
        return self._file

    def append(self, record: CompressionRecord) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with self._file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.model_dump(), sort_keys=True) + "\n")

    def load_all(self) -> list[CompressionRecord]:
        if not self._file.exists():
            return []
        records = []
        with self._file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(CompressionRecord(**json.loads(line)))
        return records

    def summary(self) -> dict:
        records = self.load_all()
        if not records:
            return {"total_calls": 0}
        total_before = sum(r.before_tokens for r in records)
        total_after = sum(r.after_tokens for r in records)
        total_saved = sum(r.reduction_tokens for r in records)
        avg_pct = sum(r.reduction_pct for r in records) / len(records)
        by_engine: dict[str, int] = {}
        for r in records:
            by_engine[r.engine] = by_engine.get(r.engine, 0) + 1
        return {
            "total_calls": len(records),
            "total_before_tokens": total_before,
            "total_after_tokens": total_after,
            "total_saved_tokens": total_saved,
            "avg_reduction_pct": round(avg_pct, 1),
            "by_engine": by_engine,
        }
