from dataclasses import replace

import pytest

from axon.context import contracts
from axon.mcp import server


def test_server_retrieval_profile_uses_runtime_snapshot(monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[runtime]\nmode = "bogus"\n', encoding="utf-8")
    monkeypatch.setenv("AXON_CONFIG", str(config_path))
    monkeypatch.delenv("AXON_RUNTIME_MODE", raising=False)
    monkeypatch.setattr(
        server,
        "_RUNTIME",
        replace(server._RUNTIME, mode="hybrid-local", active_profile=None),
    )

    _, _, _, mode = server._select_retrieval_strategy("q", "knowledge")

    assert mode == "hybrid-local"
    assert server._load_retrieval_profile()[1] == "hybrid-local"


def test_contract_retrieval_profile_without_runtime_reads_config(monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[runtime]\nmode = "bogus"\n', encoding="utf-8")
    monkeypatch.setenv("AXON_CONFIG", str(config_path))
    monkeypatch.delenv("AXON_RUNTIME_MODE", raising=False)

    with pytest.raises(ValueError):
        contracts.load_retrieval_profile()
