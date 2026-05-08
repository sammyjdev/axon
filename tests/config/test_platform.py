from __future__ import annotations

from pathlib import Path

from prometheus.config.platform import (
    DoctorReport,
    PlatformConfig,
    _to_dotenv,
    build_doctor_report,
)
from prometheus.config.runtime import ExpansionBudgetConfig, ExpansionConfig, ExpansionPaths, RuntimeConfig


def test_to_dotenv_uses_remote_infra_when_host_is_defined(monkeypatch) -> None:
    monkeypatch.setenv("PROMETHEUS_INFRA_HOST", "desktop.local")

    config = PlatformConfig(
        platform="mac",
        embedding_providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
        ollama_flash=False,
        max_models=1,
        model_primary="gemma4:e4b",
        model_knowledge="gemma4:e4b",
        keep_alive="10m",
    )

    payload = _to_dotenv(config)

    assert "PROMETHEUS_INFRA_HOST=desktop.local" in payload
    assert "QDRANT_URL=http://desktop.local:6333" in payload
    assert "REDIS_URL=redis://desktop.local:6379" in payload
    assert "NEO4J_URI=bolt://desktop.local:7687" in payload
    assert "LANGFUSE_HOST=http://desktop.local:3000" in payload
    assert "PROMETHEUS_OLLAMA_LOCAL_HOST=http://desktop.local:11434" in payload
    assert "PROMETHEUS_OLLAMA_REMOTE_HOST=http://desktop.local:11434" in payload


def test_to_dotenv_keeps_local_defaults_without_remote_host(monkeypatch) -> None:
    monkeypatch.delenv("PROMETHEUS_INFRA_HOST", raising=False)
    monkeypatch.delenv("PROMETHEUS_DESKTOP_HOST", raising=False)

    config = PlatformConfig(
        platform="pc",
        embedding_providers=["CUDAExecutionProvider"],
        ollama_flash=True,
        max_models=2,
        model_primary="gemma4:e4b",
        model_knowledge="gemma4:26b",
        keep_alive="-1",
    )

    payload = _to_dotenv(config)

    assert "PROMETHEUS_INFRA_HOST=" not in payload
    assert "QDRANT_URL=http://" not in payload
    assert "PROMETHEUS_OLLAMA_REMOTE_HOST=" not in payload


def test_build_doctor_report_prefers_remote_infra_when_host_is_configured(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, ollama_remote_host="http://desktop.local:11434")
    platform_config = PlatformConfig(
        platform="pc",
        embedding_providers=["CUDAExecutionProvider"],
        ollama_flash=True,
        max_models=2,
        model_primary="gemma4:e4b",
        model_knowledge="gemma4:26b",
        keep_alive="-1",
    )

    report = build_doctor_report(
        runtime,
        platform_config,
        docker_available=False,
        ollama_available=False,
    )

    assert report.recommended_mode == "remote-infra"
    assert report.checks["remote_infra"] == "configured"


def test_build_doctor_report_prefers_full_local_for_gpu_pc(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    platform_config = PlatformConfig(
        platform="pc",
        embedding_providers=["CUDAExecutionProvider"],
        ollama_flash=True,
        max_models=2,
        model_primary="gemma4:e4b",
        model_knowledge="gemma4:26b",
        keep_alive="-1",
    )

    report = build_doctor_report(
        runtime,
        platform_config,
        docker_available=True,
        ollama_available=True,
    )

    assert report.recommended_mode == "full-local"


def test_build_doctor_report_prefers_hybrid_local_for_mac(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    platform_config = PlatformConfig(
        platform="mac",
        embedding_providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
        ollama_flash=False,
        max_models=1,
        model_primary="gemma4:e4b",
        model_knowledge="gemma4:e4b",
        keep_alive="10m",
    )

    report = build_doctor_report(
        runtime,
        platform_config,
        docker_available=True,
        ollama_available=True,
    )

    assert report.recommended_mode == "hybrid-local"


def test_build_doctor_report_falls_back_to_minimal_when_local_tooling_missing(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    platform_config = PlatformConfig(
        platform="pc",
        embedding_providers=["CPUExecutionProvider"],
        ollama_flash=False,
        max_models=1,
        model_primary="gemma4:e4b",
        model_knowledge="gemma4:e4b",
        keep_alive="10m",
    )

    report = build_doctor_report(
        runtime,
        platform_config,
        docker_available=False,
        ollama_available=False,
    )

    assert report.recommended_mode == "minimal"
    assert report.checks["docker"] == "missing"
    assert report.checks["ollama"] == "missing"


def _runtime(tmp_path: Path, *, ollama_remote_host: str | None = None) -> RuntimeConfig:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    engine_root.mkdir()
    vault_root.mkdir()
    expansion_root = engine_root / "data" / "expansion"
    return RuntimeConfig(
        mode="full-local",
        engine_root=engine_root,
        vault_root=vault_root,
        db_path=engine_root / "data" / "prometheus.db",
        qdrant_url="http://localhost:6333",
        redis_url="redis://localhost:6379",
        rtk_max_tokens=450,
        caveman_num_ctx=4096,
        ollama_remote_host=ollama_remote_host,
        ollama_local_host="http://127.0.0.1:11434",
        caveman_model="phi3:mini",
        classifier_cloud_model="claude-haiku-4-5-20251001",
        classifier_timeout_seconds=4.0,
        policy_version="2026-04-21",
        provider_anthropic_enabled=True,
        provider_openrouter_enabled=True,
        provider_ollama_enabled=True,
        expansion=ExpansionConfig(
            enabled=True,
            manual_trigger_only=True,
            default_contexts=("knowledge", "career", "personal"),
            allow_cloud_research=True,
            source_catalog_path=engine_root / "config" / "expansion_sources.json",
            paths=ExpansionPaths(
                root=expansion_root,
                staging_root=expansion_root / "staging",
                telemetry_root=expansion_root / "telemetry",
                budget_root=expansion_root / "budget",
            ),
            budget=ExpansionBudgetConfig(
                monthly_budget_usd=4.0,
                soft_cap_usd=3.2,
                hard_cap_usd=4.0,
            ),
        ),
    )
