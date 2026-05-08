from __future__ import annotations

from pathlib import Path

from prometheus.config.runtime import list_profiles, load_runtime_config, use_profile


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
