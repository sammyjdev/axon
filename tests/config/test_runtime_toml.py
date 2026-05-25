from __future__ import annotations

from pathlib import Path

from axon.config.runtime import load_runtime_config


def test_runtime_loads_mode_and_paths_from_axon_toml(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "axon.toml"
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
    monkeypatch.delenv("AXON_ENGINE", raising=False)
    monkeypatch.delenv("AXON_VAULT", raising=False)
    monkeypatch.delenv("AXON_RUNTIME_MODE", raising=False)
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    runtime = load_runtime_config()

    assert runtime.mode == "hybrid-local"
    assert runtime.engine_root == tmp_path / "engine"
    assert runtime.vault_root == tmp_path / "vault"


def test_runtime_env_vars_override_axon_toml(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "axon.toml"
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
    monkeypatch.setenv("AXON_CONFIG", str(config_path))
    monkeypatch.setenv("AXON_RUNTIME_MODE", "full-local")
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path / "engine-from-env"))
    monkeypatch.setenv("AXON_VAULT", str(tmp_path / "vault-from-env"))

    runtime = load_runtime_config()

    assert runtime.mode == "full-local"
    assert runtime.engine_root == tmp_path / "engine-from-env"
    assert runtime.vault_root == tmp_path / "vault-from-env"
