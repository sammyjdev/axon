from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from axon.config.runtime import RuntimeConfig, load_runtime_config


class BudgetEnforcement(str, Enum):
    CLOUD_ALLOWED = "cloud_allowed"
    LOCAL_ONLY = "local_only"
    HARD_STOP = "hard_stop"


@dataclass(frozen=True)
class ExpansionBudgetStatus:
    month: str
    spent_usd: float
    remaining_usd: float
    soft_cap_usd: float
    hard_cap_usd: float
    enforcement: BudgetEnforcement

    @property
    def cloud_allowed(self) -> bool:
        return self.enforcement is BudgetEnforcement.CLOUD_ALLOWED


@dataclass(frozen=True)
class BudgetUsageRecord:
    execution_id: str
    amount_usd: float
    model: str
    ctx: str
    topic: str | None = None
    source: str = "cloud"
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class ExpansionBudgetManager:
    def __init__(self, runtime: RuntimeConfig | None = None) -> None:
        self._runtime = runtime or load_runtime_config()
        self._config = self._runtime.expansion.budget
        self._budget_root = self._runtime.expansion.paths.budget_root

    def budget_file(self, for_date: date | None = None) -> Path:
        return self._runtime.expansion.paths.monthly_budget_file(for_date)

    def status(self, for_date: date | None = None) -> ExpansionBudgetStatus:
        month_date = for_date or date.today()
        month = month_date.strftime("%Y-%m")
        payload = self._read_budget_file(self.budget_file(month_date))
        spent = float(payload.get("spent_usd", 0.0))
        hard_cap = self._config.hard_cap_usd
        if spent >= hard_cap:
            enforcement = BudgetEnforcement.HARD_STOP
        elif spent >= self._config.soft_cap_usd:
            enforcement = BudgetEnforcement.LOCAL_ONLY
        else:
            enforcement = BudgetEnforcement.CLOUD_ALLOWED
        return ExpansionBudgetStatus(
            month=month,
            spent_usd=spent,
            remaining_usd=max(hard_cap - spent, 0.0),
            soft_cap_usd=self._config.soft_cap_usd,
            hard_cap_usd=hard_cap,
            enforcement=enforcement,
        )

    def can_use_cloud(self, for_date: date | None = None) -> bool:
        return self.status(for_date).cloud_allowed

    def record_usage(
        self,
        record: BudgetUsageRecord,
        *,
        for_date: date | None = None,
    ) -> ExpansionBudgetStatus:
        if record.amount_usd < 0:
            raise ValueError("amount_usd must be non-negative")

        month_date = for_date or self._date_from_iso(record.occurred_at)
        budget_file = self.budget_file(month_date)
        payload = self._read_budget_file(budget_file)
        entries = list(payload.get("entries", []))
        entries.append(asdict(record))
        payload = {
            "month": month_date.strftime("%Y-%m"),
            "spent_usd": round(float(payload.get("spent_usd", 0.0)) + record.amount_usd, 6),
            "soft_cap_usd": self._config.soft_cap_usd,
            "hard_cap_usd": self._config.hard_cap_usd,
            "entries": entries,
        }
        budget_file.parent.mkdir(parents=True, exist_ok=True)
        budget_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return self.status(month_date)

    @staticmethod
    def _date_from_iso(value: str) -> date:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).date()

    @staticmethod
    def _read_budget_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text())
