from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from prometheus.config.runtime import RuntimeConfig, load_runtime_config


@dataclass(frozen=True)
class TraceRecord:
    trace_id: str
    stage: str
    caller: str
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    ctx: str | None = None
    policy_decision_id: str | None = None
    policy_version: str | None = None
    route: str | None = None
    model: str | None = None
    payload: dict[str, str | int | float | bool | None] = field(default_factory=dict)


class TraceStore:
    def __init__(self, runtime: RuntimeConfig | None = None) -> None:
        self._runtime = runtime or load_runtime_config()
        self._file = self._runtime.data_root / "trace" / "records.jsonl"

    @property
    def records_file(self) -> Path:
        return self._file

    def append(self, record: TraceRecord) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with self._file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), sort_keys=True) + "\n")

    def load_all(self) -> list[TraceRecord]:
        if not self._file.exists():
            return []

        records: list[TraceRecord] = []
        with self._file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(TraceRecord(**json.loads(line)))
        return records

    def query(
        self,
        *,
        trace_id: str | None = None,
        stage: str | None = None,
        caller: str | None = None,
        ctx: str | None = None,
        policy_decision_id: str | None = None,
    ) -> list[TraceRecord]:
        records = self.load_all()
        if trace_id is not None:
            records = [record for record in records if record.trace_id == trace_id]
        if stage is not None:
            records = [record for record in records if record.stage == stage]
        if caller is not None:
            records = [record for record in records if record.caller == caller]
        if ctx is not None:
            records = [record for record in records if record.ctx == ctx]
        if policy_decision_id is not None:
            records = [
                record
                for record in records
                if record.policy_decision_id == policy_decision_id
            ]
        return records
