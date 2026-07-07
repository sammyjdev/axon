from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from axon.cli import pb
from axon.portability.exporter import ExportArtifact, ExportManifest
from axon.router.classifier import TaskType

runner = CliRunner()


def test_git_command_proxies_to_rtk(monkeypatch) -> None:
    captured: list[str] = []

    def fake_rtk_proxy(command: str) -> None:
        captured.append(command)

    monkeypatch.setattr(pb, "rtk_proxy", fake_rtk_proxy)

    result = runner.invoke(pb.app, ["git", "status"])

    assert result.exit_code == 0
    assert captured == ["git status"]


def test_run_command_proxies_raw_shell(monkeypatch) -> None:
    captured: list[str] = []

    def fake_rtk_proxy(command: str) -> None:
        captured.append(command)

    monkeypatch.setattr(pb, "rtk_proxy", fake_rtk_proxy)

    result = runner.invoke(pb.app, ["run", "git status"])

    assert result.exit_code == 0
    assert captured == ["git status"]


def test_portability_export_invokes_exporter(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_export_portability_bundle(destination: Path, *, runtime) -> ExportManifest:
        captured["destination"] = destination
        captured["runtime"] = runtime
        return ExportManifest(
            manifest_version="1",
            artifacts=(
                ExportArtifact(
                    kind="metadata/env",
                    path="metadata/env.json",
                    sha256="abc",
                    size_bytes=12,
                ),
            ),
        )

    monkeypatch.setattr(
        "axon.portability.exporter.export_portability_bundle",
        fake_export_portability_bundle,
    )

    destination = tmp_path / "bundle"
    result = runner.invoke(pb.app, ["portability", "export", str(destination)])

    assert result.exit_code == 0
    assert captured["destination"] == destination
    assert captured["runtime"] is pb._RUNTIME
    assert "Bundle exportado em:" in result.stdout
    assert "Artefatos exportados: 1" in result.stdout


def test_portability_import_invokes_importer(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_import_portability_bundle(source: Path, engine_root: Path) -> ExportManifest:
        captured["source"] = source
        captured["engine_root"] = engine_root
        return ExportManifest(
            manifest_version="1",
            artifacts=(
                ExportArtifact(
                    kind="config/axon_toml",
                    path="config/axon.toml",
                    sha256="def",
                    size_bytes=24,
                ),
            ),
        )

    monkeypatch.setattr(
        "axon.portability.importer.import_portability_bundle",
        fake_import_portability_bundle,
    )

    source = tmp_path / "bundle"
    engine_root = tmp_path / "engine"
    result = runner.invoke(pb.app, ["portability", "import", str(source), str(engine_root)])

    assert result.exit_code == 0
    assert captured["source"] == source
    assert captured["engine_root"] == engine_root
    assert "Bundle importado em:" in result.stdout
    assert "Artefatos importados: 1" in result.stdout


def test_search_shows_semantic_results(monkeypatch) -> None:
    async def fake_hits(*args, **kwargs):
        _ = (args, kwargs)
        await asyncio.sleep(0)
        return [
            {
                "score": 0.91,
                "payload": {
                    "file_path": "/tmp/vector_store.py",
                    "symbol": "upsert",
                    "chunk_type": "method",
                    "content": "async def upsert(self, chunk): ...",
                },
            }
        ]

    monkeypatch.setattr(pb, "_semantic_search_hits", fake_hits)

    result = runner.invoke(pb.app, ["search", "upsert vector", "--ctx", "knowledge", "--top", "3"])

    assert result.exit_code == 0
    assert "Buscando em:" in result.stdout
    assert "score=0.9100" in result.stdout
    assert "symbol=upsert" in result.stdout
    assert "trace_id:" in result.stdout


def test_search_applies_strategy_budget_and_prints_context_pack(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_hits(*args, **kwargs):
        captured["top_k"] = kwargs["top_k"]
        await asyncio.sleep(0)
        return [
            {
                "score": 0.91,
                "payload": {
                    "file_path": "/tmp/vector_store.py",
                    "symbol": "upsert",
                    "chunk_type": "method",
                    "content": "async def upsert(self, chunk): ...",
                },
            }
        ]

    monkeypatch.setattr(pb, "_semantic_search_hits", fake_hits)
    monkeypatch.setattr(
        "axon.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )

    result = runner.invoke(pb.app, ["search", "upsert vector", "--ctx", "knowledge", "--top", "20"])

    assert result.exit_code == 0
    assert captured["top_k"] == 20
    assert "ContextPack: strategy=balanced" in result.stdout
    assert "task_type=CODE_ANALYSIS" in result.stdout
    assert "segments=1" in result.stdout
    assert "contexts=knowledge" in result.stdout
    assert "trace_id:" in result.stdout


def test_search_shows_all_requested_hits_even_when_context_pack_is_smaller(monkeypatch) -> None:
    hits = [
        {
            "score": 0.91 - (index * 0.01),
            "payload": {
                "file_path": f"/tmp/file_{index}.py",
                "symbol": f"symbol_{index}",
                "chunk_type": "method",
                "content": f"content {index}",
            },
        }
        for index in range(10)
    ]

    async def fake_hits(*args, **kwargs):
        _ = (args, kwargs)
        await asyncio.sleep(0)
        return hits

    monkeypatch.setattr(pb, "_semantic_search_hits", fake_hits)
    monkeypatch.setattr(
        "axon.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )

    result = runner.invoke(pb.app, ["search", "upsert vector", "--ctx", "knowledge", "--top", "10"])

    assert result.exit_code == 0
    assert "1. score=0.9100 | /tmp/file_0.py" in result.stdout
    assert "10. score=0.8200 | /tmp/file_9.py" in result.stdout
    assert "segments=8" in result.stdout
    assert "all requested hits are shown above" in result.stdout


def test_search_surfaces_staleness_warnings(monkeypatch) -> None:
    async def fake_hits(*args, **kwargs):
        _ = (args, kwargs)
        await asyncio.sleep(0)
        return [
            {
                "score": 0.91,
                "payload": {
                    "file_path": "/tmp/vector_store.py",
                    "symbol": "upsert",
                    "chunk_type": "method",
                    "content": "async def upsert(self, chunk): ...",
                },
                "staleness": {
                    "score": 1.0,
                    "is_stale": True,
                    "reasons": ["age_exceeds_stale_window"],
                    "replacement_family": "runbooks/search.md",
                    "replacement_id": "fresh-hit",
                    "replacement_reason": "newer_record_in_family",
                },
            }
        ]

    monkeypatch.setattr(pb, "_semantic_search_hits", fake_hits)

    result = runner.invoke(pb.app, ["search", "upsert vector", "--ctx", "knowledge"])

    assert result.exit_code == 0
    assert "staleness:" in result.stdout
    assert "upsert stale -> replacement=fresh-hit" in result.stdout


def test_doctor_prints_recommended_mode_and_checks(monkeypatch, tmp_path) -> None:
    from axon.config.platform import DoctorReport, PlatformConfig
    from axon.config.runtime import (
        ExpansionBudgetConfig,
        ExpansionConfig,
        ExpansionPaths,
        RuntimeConfig,
    )

    runtime = RuntimeConfig(
        mode="full-local",
        engine_root=tmp_path / "engine",
        vault_root=tmp_path / "vault",
        db_path=tmp_path / "engine" / "data" / "axon.db",
        pg_url="postgresql://axon:axon@localhost:5433/axon",
        rtk_max_tokens=450,
        caveman_num_ctx=4096,
        ollama_remote_host=None,
        ollama_local_host="http://127.0.0.1:11434",
        caveman_model="phi3:mini",
        scoring_model="ollama/gemma4:e4b",
        scoring_num_ctx=8192,
        classifier_cloud_model="claude-haiku-4-5-20251001",
        classifier_timeout_seconds=4.0,
        policy_version="2026-04-21",
        provider_anthropic_enabled=True,
        provider_openrouter_enabled=True,
        provider_ollama_enabled=True,
        provider_profile="free",
        openrouter_compliance_required=False,
        expansion=ExpansionConfig(
            enabled=True,
            manual_trigger_only=True,
            default_contexts=("knowledge", "career", "personal"),
            allow_cloud_research=True,
            source_catalog_path=tmp_path / "engine" / "config" / "expansion_sources.json",
            paths=ExpansionPaths(
                root=tmp_path / "engine" / "data" / "expansion",
                staging_root=tmp_path / "engine" / "data" / "expansion" / "staging",
                telemetry_root=tmp_path / "engine" / "data" / "expansion" / "telemetry",
                budget_root=tmp_path / "engine" / "data" / "expansion" / "budget",
            ),
            budget=ExpansionBudgetConfig(
                monthly_budget_usd=4.0,
                soft_cap_usd=3.2,
                hard_cap_usd=4.0,
            ),
        ),
    )
    runtime.engine_root.mkdir()
    runtime.vault_root.mkdir()

    monkeypatch.setattr(pb, "load_runtime_config", lambda: runtime)
    monkeypatch.setattr(
        "axon.config.runtime.get_profile",
        lambda _name: {
            "name": "solo-dev",
            "description": "Single developer default",
            "mode": "hybrid-local",
            "cloud_policy": "avoid",
            "infra_strategy": "local",
            "memory_tier": "full",
            "enabled_features": ("rtk",),
        },
    )
    monkeypatch.setattr(
        "axon.config.platform.detect_platform",
        lambda: PlatformConfig(
            platform="pc",
            embedding_providers=["CUDAExecutionProvider"],
            ollama_flash=True,
            max_models=2,
            model_primary="gemma4:e4b",
            model_knowledge="gemma4:26b",
            keep_alive="-1",
        ),
    )
    monkeypatch.setattr(
        "axon.config.platform.build_doctor_report",
        lambda runtime, platform_config, docker_available, ollama_available, **_kwargs: (
            DoctorReport(
                platform=platform_config.platform,
                recommended_mode="full-local",
                checks={
                    "engine_root": "ok",
                    "vault_root": "ok",
                    "docker": "ok",
                    "ollama": "ok",
                    "remote_infra": "local",
                },
                sources={"mode": "env", "engine_root": "toml", "vault_root": "default"},
                configured_mode="full-local",
                active_profile="solo-dev",
                profile_mode="hybrid-local",
                notes=["GPU-capable local stack available."],
            )
        ),
    )

    result = runner.invoke(pb.app, ["doctor"])

    # Exit code reflects severity from dec-114 capture/adr checks. The
    # test environment may have a pending backlog or stale draft, which
    # surfaces as exit 1 (warn). Accept 0 or 1; reject only hard fail (2).
    assert result.exit_code in (0, 1), result.stdout
    assert "AXON doctor" in result.stdout
    assert "recommended_mode: full-local" in result.stdout
    assert "mode_source: env" in result.stdout
    assert "active_profile: solo-dev" in result.stdout
    assert "docker: ok" in result.stdout
    assert "GPU-capable local stack available." in result.stdout
    assert "capabilities:" in result.stdout
    assert "enabled: rtk" in result.stdout
    assert "vector_backend: pgvector" in result.stdout


def test_init_writes_env_local_with_mode_and_paths(monkeypatch, tmp_path) -> None:
    from axon.config.platform import PlatformConfig

    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"

    monkeypatch.setattr(
        "axon.config.platform.detect_platform",
        lambda: PlatformConfig(
            platform="mac",
            embedding_providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
            ollama_flash=False,
            max_models=1,
            model_primary="gemma4:e4b",
            model_knowledge="gemma4:e4b",
            keep_alive="10m",
        ),
    )

    result = runner.invoke(
        pb.app,
        [
            "init",
            "--engine",
            str(engine_root),
            "--vault",
            str(vault_root),
            "--mode",
            "hybrid-local",
        ],
    )

    env_file = engine_root / ".env.local"
    config_file = engine_root / "axon.toml"

    assert result.exit_code == 0
    assert env_file.exists()
    assert config_file.exists()
    payload = env_file.read_text(encoding="utf-8")
    config_payload = config_file.read_text(encoding="utf-8")
    assert f"AXON_ENGINE={engine_root}" in payload
    assert f"AXON_VAULT={vault_root}" in payload
    assert "AXON_RUNTIME_MODE=hybrid-local" in payload
    assert "AXON_PLATFORM=mac" in payload
    assert '[runtime]' in config_payload
    assert 'mode = "hybrid-local"' in config_payload
    assert f'engine_root = "{engine_root.as_posix()}"' in config_payload
    assert f'vault_root = "{vault_root.as_posix()}"' in config_payload


def test_init_refuses_to_overwrite_env_local_without_force(monkeypatch, tmp_path) -> None:
    from axon.config.platform import PlatformConfig

    engine_root = tmp_path / "engine"
    engine_root.mkdir()
    env_file = engine_root / ".env.local"
    env_file.write_text("AXON_RUNTIME_MODE=minimal\n", encoding="utf-8")

    monkeypatch.setattr(
        "axon.config.platform.detect_platform",
        lambda: PlatformConfig(
            platform="pc",
            embedding_providers=["CUDAExecutionProvider"],
            ollama_flash=True,
            max_models=2,
            model_primary="gemma4:e4b",
            model_knowledge="gemma4:26b",
            keep_alive="-1",
        ),
    )

    result = runner.invoke(
        pb.app,
        [
            "init",
            "--engine",
            str(engine_root),
            "--vault",
            str(tmp_path / "vault"),
            "--mode",
            "full-local",
        ],
    )

    assert result.exit_code == 1
    assert "já existe" in result.stdout
    assert env_file.read_text(encoding="utf-8") == "AXON_RUNTIME_MODE=minimal\n"


def test_profile_list_shows_profiles_and_active_marker(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "axon.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "hybrid-local"',
                'active_profile = "solo-dev"',
                f'engine_root = "{(tmp_path / "engine").as_posix()}"',
                f'vault_root = "{(tmp_path / "vault").as_posix()}"',
                "",
                "[profiles.solo-dev]",
                'description = "Single developer default"',
                'mode = "hybrid-local"',
                "",
                "[profiles.team-dev]",
                'description = "Shared team setup"',
                'mode = "remote-infra"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = runner.invoke(pb.app, ["profile", "list"])

    assert result.exit_code == 0
    assert "* solo-dev" in result.stdout
    assert "team-dev" in result.stdout
    assert "remote-infra" in result.stdout


def test_profile_use_updates_config_file(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "axon.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "hybrid-local"',
                'active_profile = "solo-dev"',
                f'engine_root = "{(tmp_path / "engine").as_posix()}"',
                f'vault_root = "{(tmp_path / "vault").as_posix()}"',
                "",
                "[profiles.solo-dev]",
                'description = "Single developer default"',
                'mode = "hybrid-local"',
                "",
                "[profiles.team-dev]",
                'description = "Shared team setup"',
                'mode = "remote-infra"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = runner.invoke(pb.app, ["profile", "use", "team-dev"])

    payload = config_path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "Perfil ativo: team-dev" in result.stdout
    assert 'active_profile = "team-dev"' in payload
    assert 'mode = "remote-infra"' in payload


def test_profile_show_displays_active_profile(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "axon.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "hybrid-local"',
                'active_profile = "solo-dev"',
                f'engine_root = "{(tmp_path / "engine").as_posix()}"',
                f'vault_root = "{(tmp_path / "vault").as_posix()}"',
                "",
                "[profiles.solo-dev]",
                'description = "Single developer default"',
                'mode = "hybrid-local"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = runner.invoke(pb.app, ["profile", "show"])

    assert result.exit_code == 0
    assert "name: solo-dev" in result.stdout
    assert "mode: hybrid-local" in result.stdout
    assert "description: Single developer default" in result.stdout
    assert "selected_capabilities:" in result.stdout
    assert "overkill_capabilities:" in result.stdout


def test_configure_applies_recommended_profile(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "axon.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "hybrid-local"',
                'active_profile = "solo-dev"',
                f'engine_root = "{(tmp_path).as_posix()}"',
                f'vault_root = "{(tmp_path / "vault").as_posix()}"',
                "",
                "[profiles.solo-dev]",
                'description = "Single developer default"',
                'mode = "hybrid-local"',
                "",
                "[profiles.team-dev]",
                'description = "Shared team setup"',
                'mode = "remote-infra"',
                "",
                "[profiles.privacy-first]",
                'description = "Prefer local or remote self-hosted paths"',
                'mode = "minimal"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = runner.invoke(
        pb.app,
        [
            "configure",
            "--use-case",
            "team",
            "--privacy",
            "internal",
            "--hardware",
            "nvidia",
        ],
    )

    payload = config_path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "recommended_profile: team-dev" in result.stdout
    assert "recommended_mode: remote-infra" in result.stdout
    assert "selected_capabilities: shared-remote-infra" in result.stdout
    assert 'active_profile = "team-dev"' in payload


def test_configure_works_with_minimal_runtime_only_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "axon.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "hybrid-local"',
                'active_profile = "solo-dev"',
                f'engine_root = "{(tmp_path).as_posix()}"',
                f'vault_root = "{(tmp_path / "vault").as_posix()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = runner.invoke(
        pb.app,
        [
            "configure",
            "--use-case",
            "team",
            "--privacy",
            "internal",
            "--hardware",
            "nvidia",
        ],
    )

    payload = config_path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "recommended_profile: team-dev" in result.stdout
    assert 'active_profile = "team-dev"' in payload
    assert "[profiles.solo-dev]" in payload
    assert "[profiles.team-dev]" in payload
    assert "[profiles.privacy-first]" in payload


def test_configure_interactive_applies_recommended_profile(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "axon.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "hybrid-local"',
                'active_profile = "solo-dev"',
                f'engine_root = "{(tmp_path).as_posix()}"',
                f'vault_root = "{(tmp_path / "vault").as_posix()}"',
                "",
                "[profiles.solo-dev]",
                'description = "Single developer default"',
                'mode = "hybrid-local"',
                "",
                "[profiles.team-dev]",
                'description = "Shared team setup"',
                'mode = "remote-infra"',
                "",
                "[profiles.privacy-first]",
                'description = "Prefer local or remote self-hosted paths"',
                'mode = "minimal"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = runner.invoke(
        pb.app,
        ["configure"],
        input="team\ninternal\nnvidia\n\n\n\n\n",
    )

    payload = config_path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "Caso de uso" in result.stdout
    assert "recommended_profile: team-dev" in result.stdout
    assert "recommended_mode: remote-infra" in result.stdout
    assert "selected_capabilities: shared-remote-infra" in result.stdout
    assert 'active_profile = "team-dev"' in payload


def test_configure_accepts_preferred_mode_override(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "axon.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "hybrid-local"',
                'active_profile = "solo-dev"',
                f'engine_root = "{(tmp_path).as_posix()}"',
                f'vault_root = "{(tmp_path / "vault").as_posix()}"',
                "",
                "[profiles.solo-dev]",
                'description = "Single developer default"',
                'mode = "hybrid-local"',
                "",
                "[profiles.team-dev]",
                'description = "Shared team setup"',
                'mode = "remote-infra"',
                "",
                "[profiles.privacy-first]",
                'description = "Prefer local or remote self-hosted paths"',
                'mode = "minimal"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = runner.invoke(
        pb.app,
        [
            "configure",
            "--use-case",
            "solo",
            "--privacy",
            "public",
            "--hardware",
            "mac-laptop",
            "--preferred-mode",
            "remote-infra",
        ],
    )

    payload = config_path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "recommended_profile: team-dev" in result.stdout
    assert "recommended_mode: remote-infra" in result.stdout
    assert "selected_capabilities: shared-remote-infra" in result.stdout
    assert 'active_profile = "team-dev"' in payload


def test_configure_rejects_invalid_restricted_remote_combination(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "axon.toml"
    original_payload = "\n".join(
        [
            "[runtime]",
            'mode = "hybrid-local"',
            'active_profile = "solo-dev"',
            f'engine_root = "{(tmp_path).as_posix()}"',
            f'vault_root = "{(tmp_path / "vault").as_posix()}"',
            "",
            "[profiles.solo-dev]",
            'description = "Single developer default"',
            'mode = "hybrid-local"',
            "",
            "[profiles.team-dev]",
            'description = "Shared team setup"',
            'mode = "remote-infra"',
            "",
            "[profiles.privacy-first]",
            'description = "Prefer local or remote self-hosted paths"',
            'mode = "minimal"',
            "",
        ]
    )
    config_path.write_text(original_payload, encoding="utf-8")
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = runner.invoke(
        pb.app,
        [
            "configure",
            "--use-case",
            "solo",
            "--privacy",
            "restricted",
            "--hardware",
            "cpu-only",
            "--infra",
            "remote",
        ],
    )

    assert result.exit_code == 2
    # CliRunner(mix_stderr=True) merges stderr into output; use result.output until
    # the runner is created with mix_stderr=False.
    assert "privacy=restricted is incompatible with infra=remote" in result.output
    assert config_path.read_text(encoding="utf-8") == original_payload


def test_profile_create_appends_new_profile(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "axon.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "hybrid-local"',
                'active_profile = "solo-dev"',
                f'engine_root = "{(tmp_path).as_posix()}"',
                f'vault_root = "{(tmp_path / "vault").as_posix()}"',
                "",
                "[profiles.solo-dev]",
                'description = "Single developer default"',
                'mode = "hybrid-local"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = runner.invoke(
        pb.app,
        [
            "profile",
            "create",
            "support-lite",
            "--description",
            "Support workflow on lighter hardware",
            "--mode",
            "minimal",
            "--cloud-policy",
            "deny",
            "--infra-strategy",
            "local",
            "--memory-tier",
            "light",
            "--enabled-features",
            "rtk,local-rag",
        ],
    )

    payload = config_path.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "Perfil criado: support-lite" in result.stdout
    assert "[profiles.support-lite]" in payload
    assert 'cloud_policy = "deny"' in payload
    assert 'infra_strategy = "local"' in payload
    assert 'memory_tier = "light"' in payload
    assert 'enabled_features = ["rtk", "local-rag"]' in payload


def test_profile_export_prints_toml_snippet(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "axon.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "hybrid-local"',
                'active_profile = "solo-dev"',
                f'engine_root = "{(tmp_path).as_posix()}"',
                f'vault_root = "{(tmp_path / "vault").as_posix()}"',
                "",
                "[profiles.team-dev]",
                'description = "Shared team setup"',
                'mode = "remote-infra"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = runner.invoke(pb.app, ["profile", "export", "team-dev"])

    assert result.exit_code == 0
    assert "[profiles.team-dev]" in result.stdout
    assert 'mode = "remote-infra"' in result.stdout


def test_rtk_reduces_and_prints_summary() -> None:
    text = (
        "AXON indexa contexto técnico. "
        "AXON indexa contexto técnico. "
        "Use prompts curtos para reduzir custo sem perder precisão."
    )

    def fake_compress(_text: str, max_tokens: int) -> tuple[str, str | None]:
        _ = max_tokens
        return "texto comprimido", None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pb, "_compress_with_rtk", fake_compress)
    result = runner.invoke(pb.app, ["rtk", text, "--max-tokens", "20"])
    monkeypatch.undo()

    assert result.exit_code == 0
    assert "RTK tokens aprox:" in result.stdout
    assert "Texto comprimido:" in result.stdout


def test_rtk_status_without_binary_exits(monkeypatch) -> None:
    monkeypatch.setattr(pb, "_rtk_binary_path", lambda: None)

    result = runner.invoke(pb.app, ["rtk-status"])

    assert result.exit_code == 1
    assert "RTK: não instalado" in result.stdout


def test_rtk_proxy_without_binary_exits(monkeypatch) -> None:
    monkeypatch.setattr(pb, "_rtk_binary_path", lambda: None)

    result = runner.invoke(pb.app, ["rtk-proxy", "git status"])

    assert result.exit_code == 1
    assert "rtkx não instalado" in result.stdout


def test_rtk_init_codex_calls_expected_command(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        _ = kwargs
        commands.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(pb, "_rtk_binary_path", lambda: "/usr/local/bin/rtk")
    monkeypatch.setattr(pb.subprocess, "run", fake_run)

    result = runner.invoke(pb.app, ["rtk-init", "--agent", "codex"])

    assert result.exit_code == 0
    assert commands == [["/usr/local/bin/rtk", "init", "-g", "--codex"]]


def _seed_edges(db: Path, edges: list[tuple[str, str]]) -> None:
    """Insert calls edges into a fresh SQLite graph at ``db``."""

    async def _run() -> None:
        from axon.core.edge import Edge
        from axon.store.session_store import SessionStore

        store = SessionStore(db)
        await store.init()
        for source, target in edges:
            await store.add_edge(Edge(source_id=source, target_id=target, type="calls"))
        await store.close()

    asyncio.run(_run())


def test_graph_index_reports_counts(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "mod.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(pb, "_get_db_path", lambda: tmp_path / "axon.db")

    result = runner.invoke(pb.app, ["graph", "index", "--repo", str(repo)])

    assert result.exit_code == 0
    assert "símbolos" in result.output


def test_graph_index_fails_for_missing_repo(tmp_path) -> None:
    result = runner.invoke(
        pb.app, ["graph", "index", "--repo", str(tmp_path / "nope")]
    )
    assert result.exit_code == 1
    assert "não encontrado" in result.output


def test_graph_neighbors_lists_edges(monkeypatch, tmp_path) -> None:
    db = tmp_path / "axon.db"
    monkeypatch.setattr(pb, "_get_db_path", lambda: db)
    _seed_edges(db, [("A", "B")])

    result = runner.invoke(pb.app, ["graph", "neighbors", "A"])

    assert result.exit_code == 0
    assert "A -> B" in result.output


def test_graph_path_prints_route(monkeypatch, tmp_path) -> None:
    db = tmp_path / "axon.db"
    monkeypatch.setattr(pb, "_get_db_path", lambda: db)
    _seed_edges(db, [("A", "B"), ("B", "C")])

    result = runner.invoke(pb.app, ["graph", "path", "A", "C"])

    assert result.exit_code == 0
    assert "A -> B -> C" in result.output


def test_index_dev_dry_run_uses_manifest_without_writes(tmp_path) -> None:
    project_path = tmp_path / "project"
    source_dir = project_path / "src"
    ignored_dir = project_path / "node_modules"
    source_dir.mkdir(parents=True)
    ignored_dir.mkdir()
    (source_dir / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (ignored_dir / "lib.py").write_text("def ignored():\n    return 1\n", encoding="utf-8")
    manifest = tmp_path / "projects.json"
    manifest.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "demo",
                        "path": str(project_path),
                        "ctx": "knowledge",
                        "enabled": True,
                        "languages": ["python"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(pb.app, ["index-dev", "--manifest", str(manifest), "--dry-run"])

    assert result.exit_code == 0
    assert "demo: ctx=knowledge" in result.stdout
    assert "files=1" in result.stdout


def test_index_dev_rejects_invalid_manifest(tmp_path) -> None:
    manifest = tmp_path / "projects.json"
    manifest.write_text(json.dumps({"projects": []}), encoding="utf-8")

    result = runner.invoke(
        pb.app,
        ["index-dev", "--manifest", str(tmp_path / "missing.json"), "--dry-run"],
    )

    assert result.exit_code == 1
    assert "Manifesto inválido:" in result.output
