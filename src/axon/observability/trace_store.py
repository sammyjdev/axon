from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from axon.config.runtime import RuntimeConfig, load_runtime_config

if TYPE_CHECKING:
    from axon.policy.core import PolicyDecision


TracePayloadValue = str | int | float | bool | None
TracePayload = dict[str, TracePayloadValue]


class TraceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    trace_id: str
    stage: str
    caller: str
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    ctx: str | None = None
    policy_decision_id: str | None = None
    policy_version: str | None = None
    route: str | None = None
    model: str | None = None
    payload: TracePayload = Field(default_factory=dict)


class TraceRecorder:
    def __init__(
        self,
        *,
        store: TraceStore,
        trace_id: str,
        caller: str,
        ctx: str | None = None,
    ) -> None:
        self._store = store
        self._trace_id = trace_id
        self._caller = caller
        self._ctx = ctx

    def append_stage(
        self,
        stage: str,
        *,
        ctx: str | None = None,
        policy_decision_id: str | None = None,
        policy_version: str | None = None,
        route: str | None = None,
        model: str | None = None,
        payload: TracePayload | None = None,
    ) -> TraceRecord:
        record = TraceRecord(
            trace_id=self._trace_id,
            stage=stage,
            caller=self._caller,
            ctx=self._ctx if ctx is None else ctx,
            policy_decision_id=policy_decision_id,
            policy_version=policy_version,
            route=route,
            model=model,
            payload={} if payload is None else payload,
        )
        self._store.append(record)
        return record

    def append_policy_decision(
        self,
        decision: PolicyDecision,
        *,
        payload: TracePayload | None = None,
        stage: str = "policy",
    ) -> TraceRecord:
        merged_payload: TracePayload = {
            **decision.metadata,
            "allowed": decision.allowed,
            "reason_code": decision.reason_code.value,
            "sensitivity": decision.sensitivity.value,
        }
        if payload is not None:
            merged_payload.update(payload)

        return self.append_stage(
            stage,
            ctx=decision.ctx,
            policy_decision_id=decision.decision_id,
            policy_version=decision.policy_version,
            route=decision.route.value,
            model=decision.model,
            payload=merged_payload,
        )


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
            fh.write(json.dumps(record.model_dump(), sort_keys=True) + "\n")

    def recorder(
        self,
        *,
        trace_id: str,
        caller: str,
        ctx: str | None = None,
    ) -> TraceRecorder:
        return TraceRecorder(store=self, trace_id=trace_id, caller=caller, ctx=ctx)

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
