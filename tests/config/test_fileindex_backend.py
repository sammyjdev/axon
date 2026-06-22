from __future__ import annotations

import pytest


def test_fileindex_backend_defaults_to_postgres(monkeypatch) -> None:
    monkeypatch.delenv("AXON_FILEINDEX_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().fileindex_backend == "postgres"


def test_fileindex_backend_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AXON_FILEINDEX_BACKEND", "postgres")
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().fileindex_backend == "postgres"


def test_fileindex_backend_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("AXON_FILEINDEX_BACKEND", "mongodb")
    from axon.config.runtime import load_runtime_config

    with pytest.raises(ValueError):
        load_runtime_config()
