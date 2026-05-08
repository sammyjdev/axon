from __future__ import annotations

import pytest

from prometheus.config.runtime import load_runtime_config


def test_runtime_defaults_to_full_local_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(tmp_path / "engine"))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(tmp_path / "vault"))

    runtime = load_runtime_config()

    assert runtime.mode == "full-local"


@pytest.mark.parametrize(
    "mode",
    ("full-local", "hybrid-local", "remote-infra", "minimal"),
)
def test_runtime_accepts_all_roadmap_modes(monkeypatch, tmp_path, mode: str) -> None:
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(tmp_path / "engine"))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("PROMETHEUS_RUNTIME_MODE", mode)

    runtime = load_runtime_config()

    assert runtime.mode == mode


def test_runtime_rejects_unknown_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(tmp_path / "engine"))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("PROMETHEUS_RUNTIME_MODE", "planet-scale")

    with pytest.raises(ValueError, match="Invalid PROMETHEUS_RUNTIME_MODE"):
        load_runtime_config()
