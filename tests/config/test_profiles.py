from __future__ import annotations

from pathlib import Path

from prometheus.config.runtime import (
    create_profile,
    export_profile,
    get_profile,
    list_profiles,
    load_runtime_config,
    use_profile,
)


def test_list_profiles_reads_profile_metadata(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("PROMETHEUS_CONFIG", str(config_path))

    profiles = list_profiles()

    assert profiles == [
        ("solo-dev", "Single developer default", "hybrid-local"),
        ("team-dev", "Shared team setup", "remote-infra"),
    ]


def test_use_profile_sets_active_profile_and_runtime_mode(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("PROMETHEUS_CONFIG", str(config_path))
    monkeypatch.delenv("PROMETHEUS_RUNTIME_MODE", raising=False)

    use_profile("team-dev")
    runtime = load_runtime_config()
    payload = config_path.read_text(encoding="utf-8")

    assert runtime.mode == "remote-infra"
    assert 'active_profile = "team-dev"' in payload
    assert 'mode = "remote-infra"' in payload


def test_use_profile_syncs_env_local_when_present(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "PROMETHEUS_ENGINE=/tmp/engine",
                "PROMETHEUS_RUNTIME_MODE=hybrid-local",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROMETHEUS_CONFIG", str(config_path))

    use_profile("team-dev")

    payload = env_file.read_text(encoding="utf-8")
    assert "PROMETHEUS_RUNTIME_MODE=remote-infra" in payload


def test_get_profile_returns_metadata_for_active_profile(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("PROMETHEUS_CONFIG", str(config_path))

    profile = get_profile("solo-dev")

    assert profile == {
        "name": "solo-dev",
        "description": "Single developer default",
        "mode": "hybrid-local",
        "cloud_policy": None,
        "infra_strategy": None,
        "memory_tier": None,
        "enabled_features": (),
    }


def test_create_profile_appends_new_profile_to_toml(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("PROMETHEUS_CONFIG", str(config_path))

    create_profile(
        "support-lite",
        description="Support workflow on lighter hardware",
        mode="minimal",
    )

    payload = config_path.read_text(encoding="utf-8")
    assert "[profiles.support-lite]" in payload
    assert 'description = "Support workflow on lighter hardware"' in payload
    assert 'mode = "minimal"' in payload


def test_create_profile_persists_optional_structured_fields(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("PROMETHEUS_CONFIG", str(config_path))

    create_profile(
        "support-lite",
        description="Support workflow on lighter hardware",
        mode="minimal",
        cloud_policy="deny",
        infra_strategy="local",
        memory_tier="light",
        enabled_features=("rtk", "local-rag"),
    )

    payload = config_path.read_text(encoding="utf-8")
    assert 'cloud_policy = "deny"' in payload
    assert 'infra_strategy = "local"' in payload
    assert 'memory_tier = "light"' in payload
    assert 'enabled_features = ["rtk", "local-rag"]' in payload


def test_export_profile_returns_toml_snippet(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("PROMETHEUS_CONFIG", str(config_path))

    exported = export_profile("team-dev")

    assert "[profiles.team-dev]" in exported
    assert 'description = "Shared team setup"' in exported
    assert 'mode = "remote-infra"' in exported


def test_get_profile_reads_optional_structured_fields(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n".join(
            [
                "[profiles.privacy-first]",
                'description = "Prefer local or remote self-hosted paths"',
                'mode = "minimal"',
                'cloud_policy = "deny"',
                'infra_strategy = "local"',
                'memory_tier = "light"',
                'enabled_features = ["rtk", "local-rag"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROMETHEUS_CONFIG", str(config_path))

    profile = get_profile("privacy-first")

    assert profile == {
        "name": "privacy-first",
        "description": "Prefer local or remote self-hosted paths",
        "mode": "minimal",
        "cloud_policy": "deny",
        "infra_strategy": "local",
        "memory_tier": "light",
        "enabled_features": ("rtk", "local-rag"),
    }


def test_export_profile_includes_optional_structured_fields(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("PROMETHEUS_CONFIG", str(config_path))

    create_profile(
        "support-lite",
        description="Support workflow on lighter hardware",
        mode="minimal",
        cloud_policy="deny",
        infra_strategy="local",
        memory_tier="light",
        enabled_features=("rtk", "local-rag"),
    )

    exported = export_profile("support-lite")

    assert 'cloud_policy = "deny"' in exported
    assert 'infra_strategy = "local"' in exported
    assert 'memory_tier = "light"' in exported
    assert 'enabled_features = ["rtk", "local-rag"]' in exported


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "prometheus.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "hybrid-local"',
                'active_profile = "solo-dev"',
                f'engine_root = "{tmp_path / "engine"}"',
                f'vault_root = "{tmp_path / "vault"}"',
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
    return config_path
