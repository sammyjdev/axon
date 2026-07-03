"""Per-request recall telemetry for the OpenAI-compatible endpoint.

One JSONL record per /v1/chat/completions request, with the provider's real
token usage and the include_context flag. This is the evidence source for
the recall-cost claim ("+E input tokens/turn") and for the living metrics
page: gnomon-eval reads it via the compare module's --telemetry option.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from axon.config.runtime import RuntimeConfig, load_runtime_config


class RecallRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    ts: str
    caller: str  # "http" today; other transports may write here later
    include_context: bool
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    # "provider" = real usage from the LLM provider; "estimate" = len//4
    # fallback (LLM failure or provider without usage). Eval runs are only
    # valid when every record in the window says "provider".
    usage_source: Literal["provider", "estimate"]


class RecallTelemetryStore:
    def __init__(self, runtime: RuntimeConfig | None = None) -> None:
        self._runtime = runtime or load_runtime_config()
        self._file = self._runtime.data_root / "recall" / "requests.jsonl"

    @property
    def stats_file(self) -> Path:
        return self._file

    def append(self, record: RecallRecord) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with self._file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.model_dump(), sort_keys=True) + "\n")

    def load_all(self) -> list[RecallRecord]:
        if not self._file.exists():
            return []
        records = []
        with self._file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(RecallRecord(**json.loads(line)))
        return records
