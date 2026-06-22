from __future__ import annotations

import pytest


def test_decisions_backend_defaults_to_sqlite(monkeypatch) -> None:
    monkeypatch.delenv("AXON_DECISIONS_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().decisions_backend == "sqlite"


def test_decisions_backend_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AXON_DECISIONS_BACKEND", "postgres")
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().decisions_backend == "postgres"


def test_decisions_backend_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("AXON_DECISIONS_BACKEND", "dynamo")
    from axon.config.runtime import load_runtime_config

    with pytest.raises(ValueError):
        load_runtime_config()
