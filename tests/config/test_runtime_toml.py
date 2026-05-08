from __future__ import annotations

from pathlib import Path

from prometheus.config.runtime import load_runtime_config


def test_runtime_loads_mode_and_paths_from_prometheus_toml(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "prometheus.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "hybrid-local"',
                f'engine_root = "{tmp_path / "engine"}"',
                f'vault_root = "{tmp_path / "vault"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("PROMETHEUS_ENGINE", raising=False)
    monkeypatch.delenv("PROMETHEUS_VAULT", raising=False)
    monkeypatch.delenv("PROMETHEUS_RUNTIME_MODE", raising=False)
    monkeypatch.setenv("PROMETHEUS_CONFIG", str(config_path))

    runtime = load_runtime_config()

    assert runtime.mode == "hybrid-local"
    assert runtime.engine_root == tmp_path / "engine"
    assert runtime.vault_root == tmp_path / "vault"


def test_runtime_env_vars_override_prometheus_toml(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "prometheus.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "minimal"',
                f'engine_root = "{tmp_path / "engine-from-toml"}"',
                f'vault_root = "{tmp_path / "vault-from-toml"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROMETHEUS_CONFIG", str(config_path))
    monkeypatch.setenv("PROMETHEUS_RUNTIME_MODE", "full-local")
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(tmp_path / "engine-from-env"))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(tmp_path / "vault-from-env"))

    runtime = load_runtime_config()

    assert runtime.mode == "full-local"
    assert runtime.engine_root == tmp_path / "engine-from-env"
    assert runtime.vault_root == tmp_path / "vault-from-env"
