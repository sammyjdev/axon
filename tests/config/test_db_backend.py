# tests/config/test_db_backend.py
from __future__ import annotations


def test_db_backend_flips_all_concerns(monkeypatch) -> None:
    for v in (
        "AXON_FILEINDEX_BACKEND",
        "AXON_GRAPH_BACKEND",
        "AXON_DECISIONS_BACKEND",
        "AXON_SESSIONS_BACKEND",
    ):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("AXON_DB_BACKEND", "sqlite")
    from axon.config.runtime import load_runtime_config

    rt = load_runtime_config()
    assert rt.fileindex_backend == "sqlite"
    assert rt.graph_backend == "sqlite"
    assert rt.decisions_backend == "sqlite"
    assert rt.sessions_backend == "sqlite"


def test_per_concern_overrides_db_backend(monkeypatch) -> None:
    monkeypatch.setenv("AXON_DB_BACKEND", "sqlite")
    monkeypatch.setenv("AXON_GRAPH_BACKEND", "postgres")
    from axon.config.runtime import load_runtime_config

    rt = load_runtime_config()
    assert rt.graph_backend == "postgres"  # per-concern wins
    assert rt.decisions_backend == "sqlite"  # falls back to AXON_DB_BACKEND
