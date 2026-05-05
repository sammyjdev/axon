from __future__ import annotations

from prometheus.config.platform import PlatformConfig, _to_dotenv


def test_to_dotenv_uses_remote_infra_when_host_is_defined(monkeypatch) -> None:
    monkeypatch.setenv("PROMETHEUS_INFRA_HOST", "desktop.local")

    config = PlatformConfig(
        platform="mac",
        embedding_providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
        ollama_flash=False,
        max_models=1,
        model_primary="gemma4:e4b",
        model_knowledge="gemma4:e4b",
        keep_alive="10m",
    )

    payload = _to_dotenv(config)

    assert "PROMETHEUS_INFRA_HOST=desktop.local" in payload
    assert "QDRANT_URL=http://desktop.local:6333" in payload
    assert "REDIS_URL=redis://desktop.local:6379" in payload
    assert "NEO4J_URI=bolt://desktop.local:7687" in payload
    assert "LANGFUSE_HOST=http://desktop.local:3000" in payload
    assert "PROMETHEUS_OLLAMA_LOCAL_HOST=http://desktop.local:11434" in payload
    assert "PROMETHEUS_OLLAMA_REMOTE_HOST=http://desktop.local:11434" in payload


def test_to_dotenv_keeps_local_defaults_without_remote_host(monkeypatch) -> None:
    monkeypatch.delenv("PROMETHEUS_INFRA_HOST", raising=False)
    monkeypatch.delenv("PROMETHEUS_DESKTOP_HOST", raising=False)

    config = PlatformConfig(
        platform="pc",
        embedding_providers=["CUDAExecutionProvider"],
        ollama_flash=True,
        max_models=2,
        model_primary="gemma4:e4b",
        model_knowledge="gemma4:26b",
        keep_alive="-1",
    )

    payload = _to_dotenv(config)

    assert "PROMETHEUS_INFRA_HOST=" not in payload
    assert "QDRANT_URL=http://" not in payload
    assert "PROMETHEUS_OLLAMA_REMOTE_HOST=" not in payload
