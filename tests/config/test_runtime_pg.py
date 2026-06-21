from __future__ import annotations


def test_pg_url_defaults(monkeypatch) -> None:
    monkeypatch.delenv("AXON_PG_URL", raising=False)
    from axon.config.runtime import load_runtime_config

    cfg = load_runtime_config()
    assert cfg.pg_url == "postgresql://axon:axon@localhost:5433/axon"


def test_pg_url_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AXON_PG_URL", "postgresql://u:p@host:5432/db")
    from axon.config.runtime import load_runtime_config

    cfg = load_runtime_config()
    assert cfg.pg_url == "postgresql://u:p@host:5432/db"
