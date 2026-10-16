from __future__ import annotations


from typer.testing import CliRunner

from axon.cli import pb

runner = CliRunner()




def test_doctor_prints_recommended_mode_and_checks(monkeypatch, tmp_path) -> None:
    from axon.config.platform import DoctorReport, PlatformConfig
    from axon.config.runtime import RuntimeConfig

    runtime = RuntimeConfig(
        mode="full-local",
        engine_root=tmp_path / "engine",
        vault_root=tmp_path / "vault",
        db_path=tmp_path / "engine" / "data" / "axon.db",
        pg_url="postgresql://axon:axon@localhost:5433/axon",
        ollama_remote_host=None,
        policy_version="2026-04-21",
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
    assert "vector_backend" not in result.stdout


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
