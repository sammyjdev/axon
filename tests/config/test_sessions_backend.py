from __future__ import annotations

import pytest


def test_sessions_backend_defaults_to_sqlite(monkeypatch) -> None:
    monkeypatch.delenv("AXON_SESSIONS_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().sessions_backend == "sqlite"


def test_sessions_backend_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AXON_SESSIONS_BACKEND", "postgres")
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().sessions_backend == "postgres"


def test_sessions_backend_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("AXON_SESSIONS_BACKEND", "dynamo")
    from axon.config.runtime import load_runtime_config

    with pytest.raises(ValueError):
        load_runtime_config()
