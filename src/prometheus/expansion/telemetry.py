from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prometheus.config.runtime import RuntimeConfig, load_runtime_config


@dataclass(frozen=True)
class ExpansionExecutionRecord:
    execution_id: str
    ctx: str
    topic: str
    mode: str
    status: str
    used_cloud: bool
    cloud_cost_usd: float
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    staging_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ExpansionTelemetryStore:
    def __init__(self, runtime: RuntimeConfig | None = None) -> None:
        self._runtime = runtime or load_runtime_config()
        self._telemetry_file = self._runtime.expansion.paths.execution_telemetry_file

    @property
    def telemetry_file(self) -> Path:
        return self._telemetry_file

    def append(self, record: ExpansionExecutionRecord) -> Path:
        self._telemetry_file.parent.mkdir(parents=True, exist_ok=True)
        with self._telemetry_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")
        return self._telemetry_file
