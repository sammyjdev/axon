from __future__ import annotations

from pathlib import Path

from axon.config.platform import (
    PlatformConfig,
    _to_dotenv,
    build_doctor_report,
    build_setup_plan,
    merge_env_text,
)
from axon.config.runtime import (
    ExpansionBudgetConfig,
    ExpansionConfig,
    ExpansionPaths,
    RuntimeConfig,
)


def test_to_dotenv_uses_remote_infra_when_host_is_defined(monkeypatch) -> None:
    monkeypatch.setenv("AXON_INFRA_HOST", "desktop.local")

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

    assert "AXON_INFRA_HOST=desktop.local" in payload
    assert "QDRANT_URL=http://desktop.local:6333" in payload
    assert "REDIS_URL=redis://desktop.local:6379" in payload
    # dec-101: Neo4j was dropped; no NEO4J_URI is emitted to .env any more.
    assert "NEO4J_URI" not in payload
    assert "LANGFUSE_HOST=http://desktop.local:3000" in payload
    assert "AXON_OLLAMA_LOCAL_HOST=http://desktop.local:11434" in payload
    assert "AXON_OLLAMA_REMOTE_HOST=http://desktop.local:11434" in payload


def test_to_dotenv_keeps_local_defaults_without_remote_host(monkeypatch) -> None:
    monkeypatch.delenv("AXON_INFRA_HOST", raising=False)
    monkeypatch.delenv("AXON_DESKTOP_HOST", raising=False)

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

    assert "AXON_INFRA_HOST=" not in payload
    assert "QDRANT_URL=http://" not in payload
    assert "AXON_OLLAMA_REMOTE_HOST=" not in payload


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


def test_build_doctor_report_falls_back_to_minimal_when_local_tooling_missing(
    tmp_path: Path,
) -> None:
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


def test_build_doctor_report_warns_when_profile_mode_differs_from_runtime(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, active_profile="team-dev")
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
        profile_mode="remote-infra",
        sources={"mode": "toml", "engine_root": "toml", "vault_root": "env"},
    )

    assert report.active_profile == "team-dev"
    assert report.profile_mode == "remote-infra"
    assert report.sources["mode"] == "toml"
    assert any("Profile 'team-dev'" in note for note in report.notes)


def test_build_setup_plan_for_remote_infra_skips_local_stack() -> None:
    platform_config = PlatformConfig(
        platform="pc",
        embedding_providers=["CUDAExecutionProvider"],
        ollama_flash=True,
        max_models=2,
        model_primary="gemma4:e4b",
        model_knowledge="gemma4:26b",
        keep_alive="-1",
    )

    plan = build_setup_plan(
        runtime_mode="remote-infra",
        platform_config=platform_config,
        remote_infra_host="desktop.local",
    )

    assert plan.compose_profile is None
    assert plan.start_local_stack is False
    assert plan.pull_models == ()
    assert plan.validate_remote_services is True


def test_build_setup_plan_for_minimal_mode_skips_local_stack() -> None:
    platform_config = PlatformConfig(
        platform="mac",
        embedding_providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
        ollama_flash=False,
        max_models=1,
        model_primary="gemma4:e4b",
        model_knowledge="gemma4:e4b",
        keep_alive="10m",
    )

    plan = build_setup_plan(
        runtime_mode="minimal",
        platform_config=platform_config,
        remote_infra_host=None,
    )

    assert plan.compose_profile is None
    assert plan.start_local_stack is False
    assert plan.pull_models == ()
    assert plan.validate_remote_services is False


def test_build_setup_plan_for_hybrid_local_uses_cpu_profile_and_small_models() -> None:
    platform_config = PlatformConfig(
        platform="mac",
        embedding_providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
        ollama_flash=False,
        max_models=1,
        model_primary="gemma4:e4b",
        model_knowledge="gemma4:e4b",
        keep_alive="10m",
    )

    plan = build_setup_plan(
        runtime_mode="hybrid-local",
        platform_config=platform_config,
        remote_infra_host=None,
    )

    assert plan.compose_profile == "cpu"
    assert plan.start_local_stack is True
    assert plan.pull_models == ("phi3:mini", "gemma4:e4b")


def test_build_setup_plan_for_full_local_includes_heavier_model_when_supported() -> None:
    platform_config = PlatformConfig(
        platform="pc",
        embedding_providers=["CUDAExecutionProvider"],
        ollama_flash=True,
        max_models=2,
        model_primary="gemma4:e4b",
        model_knowledge="gemma4:26b",
        keep_alive="-1",
    )

    plan = build_setup_plan(
        runtime_mode="full-local",
        platform_config=platform_config,
        remote_infra_host=None,
    )

    assert plan.compose_profile == "gpu"
    assert plan.start_local_stack is True
    assert "gemma4:26b" in plan.pull_models


def test_merge_env_text_replaces_generated_values_with_existing_overrides() -> None:
    source = "AXON_VAULT=/custom/vault\nANTHROPIC_API_KEY=secret\n"
    target = "AXON_VAULT=~/vault\nAXON_PLATFORM=mac\n"

    merged = merge_env_text(source, target, mode="replace")

    assert "AXON_VAULT=/custom/vault" in merged
    assert "AXON_PLATFORM=mac" in merged
    assert "ANTHROPIC_API_KEY=secret" in merged


def test_merge_env_text_appends_only_missing_defaults() -> None:
    source = "AXON_ENGINE=~/dev/axon\nAXON_VAULT=~/vault\n"
    target = "AXON_ENGINE=/already/set\nAXON_PLATFORM=pc\n"

    merged = merge_env_text(source, target, mode="append-missing")

    assert "AXON_ENGINE=/already/set" in merged
    assert "AXON_VAULT=~/vault" in merged
    assert "AXON_PLATFORM=pc" in merged


def _runtime(
    tmp_path: Path,
    *,
    ollama_remote_host: str | None = None,
    active_profile: str | None = None,
) -> RuntimeConfig:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    engine_root.mkdir()
    vault_root.mkdir()
    expansion_root = engine_root / "data" / "expansion"
    return RuntimeConfig(
        mode="full-local",
        engine_root=engine_root,
        vault_root=vault_root,
        db_path=engine_root / "data" / "axon.db",
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
        active_profile=active_profile,
    )
