from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from prometheus.cli import pb


runner = CliRunner()


def test_search_shows_semantic_results(monkeypatch) -> None:
    async def fake_hits(*args, **kwargs):
        _ = (args, kwargs)
        await asyncio.sleep(0)
        return [
            {
                "score": 0.91,
                "payload": {
                    "file_path": "/tmp/vector_store.py",
                    "symbol": "upsert",
                    "chunk_type": "method",
                    "content": "async def upsert(self, chunk): ...",
                },
            }
        ]

    monkeypatch.setattr(pb, "_semantic_search_hits", fake_hits)

    result = runner.invoke(pb.app, ["search", "upsert vector", "--ctx", "knowledge", "--top", "3"])

    assert result.exit_code == 0
    assert "Buscando em:" in result.stdout
    assert "score=0.9100" in result.stdout
    assert "symbol=upsert" in result.stdout


def test_ask_uses_detected_context_and_builds_summary(monkeypatch, tmp_path) -> None:
    class FakeDetector:
        def __init__(self, *_args, **_kwargs) -> None:
            # Test double intentionally keeps no state.
            return None

        def detect(self, *_args, **_kwargs):
            return SimpleNamespace(context="knowledge", display="[knowledge 50%]")

    async def fake_hits(*args, **kwargs):
        _ = (args, kwargs)
        await asyncio.sleep(0)
        return [
            {
                "score": 0.77,
                "payload": {
                    "file_path": "/tmp/collections.py",
                    "symbol": "get_search_collections",
                    "content": "def get_search_collections(ctx): ...",
                },
            }
        ]

    monkeypatch.setenv("PROMETHEUS_ENGINE", str(tmp_path))
    monkeypatch.setattr("prometheus.context.detector.ContextDetector", FakeDetector)
    monkeypatch.setattr(pb, "_semantic_search_hits", fake_hits)

    result = runner.invoke(
        pb.app,
        [
            "ask",
            "como funciona busca por contexto",
            "--ctx",
            "knowledge",
            "--cwd",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Contexto detectado: [knowledge 50%]" in result.stdout
    assert "Contexto relevante:" in result.stdout
    assert "Síntese inicial:" in result.stdout


def test_index_reports_processed_counts(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {"ensure": False, "closed": False}

    class FakeStore:
        def __init__(self, url: str) -> None:
            self.url = url

        async def ensure_collections(self) -> None:
            await asyncio.sleep(0)
            calls["ensure"] = True

        async def close(self) -> None:
            await asyncio.sleep(0)
            calls["closed"] = True

    class FakeEngine:
        pass

    async def fake_index_path(target: Path, **_kwargs):
        await asyncio.sleep(0)
        assert target.exists()
        return 2, 7

    monkeypatch.setattr("prometheus.store.vector_store.VectorStore", FakeStore)
    monkeypatch.setattr("prometheus.embedder.engine.EmbedderEngine", FakeEngine)
    monkeypatch.setattr("prometheus.embedder.pipeline.index_path", fake_index_path)

    target = tmp_path / "knowledge"
    target.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(pb.app, ["index", str(target), "--ctx", "knowledge"])

    assert result.exit_code == 0
    assert "Indexação concluída: 2 arquivo(s), 7 chunk(s)" in result.stdout
    assert calls["ensure"] is True
    assert calls["closed"] is True


def test_watch_reindexes_changed_files(monkeypatch, tmp_path) -> None:
    class FakeStore:
        def __init__(self, url: str) -> None:
            self.url = url

        async def ensure_collections(self) -> None:
            await asyncio.sleep(0)

        async def close(self) -> None:
            await asyncio.sleep(0)

    class FakeEngine:
        pass

    async def fake_index_path(_target: Path, **_kwargs):
        await asyncio.sleep(0)
        return 1, 3

    async def fake_run_watcher(_vault_path: Path, on_file):
        await asyncio.sleep(0)
        await on_file(Path("/tmp/changed.md"))

    watch_target = tmp_path / "knowledge"
    watch_target.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("prometheus.store.vector_store.VectorStore", FakeStore)
    monkeypatch.setattr("prometheus.embedder.engine.EmbedderEngine", FakeEngine)
    monkeypatch.setattr("prometheus.embedder.pipeline.index_path", fake_index_path)
    monkeypatch.setattr("prometheus.watcher.main.run_watcher", fake_run_watcher)

    result = runner.invoke(pb.app, ["watch", str(watch_target), "--ctx", "knowledge"])

    assert result.exit_code == 0
    assert "Watcher ativo em:" in result.stdout
    assert "[watch] Reindexado:" in result.stdout
