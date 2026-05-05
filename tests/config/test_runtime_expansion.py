from __future__ import annotations

import json
from datetime import date

from prometheus.config.runtime import load_runtime_config
from prometheus.expansion.budget import (
    BudgetEnforcement,
    BudgetUsageRecord,
    ExpansionBudgetManager,
)
from prometheus.expansion.telemetry import ExpansionExecutionRecord, ExpansionTelemetryStore
from prometheus.memory.config import Mem0Config


def test_runtime_loads_expansion_defaults(monkeypatch, tmp_path) -> None:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(engine_root))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(vault_root))

    runtime = load_runtime_config()

    assert runtime.data_root == engine_root / "data"
    assert runtime.expansion.manual_trigger_only is True
    assert runtime.expansion.default_contexts == ("knowledge", "career", "personal")
    assert runtime.expansion.paths.root == engine_root / "data" / "expansion"
    assert runtime.expansion.source_catalog_path == (
        engine_root / "config" / "expansion_sources.json"
    )
    assert runtime.expansion.paths.staging_context_root("Knowledge") == (
        engine_root / "data" / "expansion" / "staging" / "knowledge"
    )
    assert runtime.vault_context_root("career") == vault_root / "career"


def test_mem0_config_prefers_qdrant_url_over_legacy_host(monkeypatch) -> None:
    monkeypatch.setenv("QDRANT_URL", "http://docker-host.local:6333")
    monkeypatch.setenv("QDRANT_HOST", "localhost")
    monkeypatch.setenv("QDRANT_PORT", "6333")

    cfg = Mem0Config()

    assert cfg.qdrant_host == "docker-host.local"
    assert cfg.qdrant_port == 6333


def test_runtime_supports_remote_desktop_infra(monkeypatch, tmp_path) -> None:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(engine_root))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(vault_root))
    monkeypatch.setenv("QDRANT_URL", "http://desktop.local:6333")
    monkeypatch.setenv("REDIS_URL", "redis://desktop.local:6379")
    monkeypatch.setenv("NEO4J_URI", "bolt://desktop.local:7687")
    monkeypatch.setenv("PROMETHEUS_OLLAMA_LOCAL_HOST", "http://desktop.local:11434")
    monkeypatch.setenv("PROMETHEUS_OLLAMA_REMOTE_HOST", "http://desktop.local:11434")

    runtime = load_runtime_config()

    assert runtime.qdrant_url == "http://desktop.local:6333"
    assert runtime.redis_url == "redis://desktop.local:6379"
    assert runtime.ollama_local_host == "http://desktop.local:11434"
    assert runtime.ollama_remote_host == "http://desktop.local:11434"


def test_budget_manager_enforces_soft_cap_and_hard_stop(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(tmp_path / "engine"))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(tmp_path / "vault"))

    runtime = load_runtime_config()
    manager = ExpansionBudgetManager(runtime)
    month = date(2026, 4, 1)

    assert manager.status(month).enforcement is BudgetEnforcement.CLOUD_ALLOWED

    after_soft_cap = manager.record_usage(
        BudgetUsageRecord(
            execution_id="run-soft",
            amount_usd=3.2,
            model="claude-haiku-4-5-20251001",
            ctx="knowledge",
            occurred_at="2026-04-10T12:00:00+00:00",
        ),
        for_date=month,
    )
    assert after_soft_cap.enforcement is BudgetEnforcement.LOCAL_ONLY
    assert after_soft_cap.cloud_allowed is False

    after_hard_stop = manager.record_usage(
        BudgetUsageRecord(
            execution_id="run-hard",
            amount_usd=0.8,
            model="claude-haiku-4-5-20251001",
            ctx="knowledge",
            occurred_at="2026-04-12T12:00:00+00:00",
        ),
        for_date=month,
    )
    assert after_hard_stop.enforcement is BudgetEnforcement.HARD_STOP
    assert after_hard_stop.remaining_usd == 0.0

    payload = json.loads(manager.budget_file(month).read_text())
    assert payload["spent_usd"] == 4.0
    assert [entry["execution_id"] for entry in payload["entries"]] == ["run-soft", "run-hard"]


def test_telemetry_store_appends_execution_records(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(tmp_path / "engine"))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(tmp_path / "vault"))

    store = ExpansionTelemetryStore(load_runtime_config())
    output = store.append(
        ExpansionExecutionRecord(
            execution_id="run-123",
            ctx="career",
            topic="system design",
            mode="fast",
            status="staged",
            used_cloud=False,
            cloud_cost_usd=0.0,
            staging_path="/tmp/staging.md",
            metadata={"source_count": 3},
        )
    )

    lines = output.read_text().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["execution_id"] == "run-123"
    assert payload["metadata"]["source_count"] == 3
