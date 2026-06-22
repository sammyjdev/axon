from __future__ import annotations

import pytest


def test_graph_backend_defaults_to_sqlite(monkeypatch) -> None:
    monkeypatch.delenv("AXON_GRAPH_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().graph_backend == "sqlite"


def test_graph_backend_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AXON_GRAPH_BACKEND", "postgres")
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().graph_backend == "postgres"


def test_graph_backend_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("AXON_GRAPH_BACKEND", "neo4j")
    from axon.config.runtime import load_runtime_config

    with pytest.raises(ValueError):
        load_runtime_config()
