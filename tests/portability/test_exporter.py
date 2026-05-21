from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from axon.portability.exporter import EXPORT_MANIFEST_VERSION, export_portability_bundle


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_export_portability_bundle_writes_manifest_and_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine_root = tmp_path / "engine"
    data_root = engine_root / "data"
    trace_file = data_root / "trace" / "records.jsonl"
    failures_db = data_root / "failures.db"
    outcomes_db = data_root / "outcomes.db"
    config_path = tmp_path / "prometheus.toml"
    export_root = tmp_path / "export"

    trace_file.parent.mkdir(parents=True, exist_ok=True)
    trace_file.write_text('{"trace_id":"trace-1"}\n', encoding="utf-8")
    failures_db.write_bytes(b"failures-db")
    outcomes_db.write_bytes(b"outcomes-db")
    config_path.write_text("[runtime]\nmode = \"hybrid-local\"\n", encoding="utf-8")

    monkeypatch.setenv("AXON_CONFIG", str(config_path))
    monkeypatch.setenv("AXON_ENGINE", str(engine_root))
    monkeypatch.setenv("AXON_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("AXON_RUNTIME_MODE", "hybrid-local")
    monkeypatch.setenv("AXON_OLLAMA_LOCAL_HOST", "http://127.0.0.1:11434")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-should-not-export")

    manifest = export_portability_bundle(
        export_root,
        runtime=SimpleNamespace(engine_root=engine_root, data_root=data_root),
    )

    exported_config = export_root / "config" / "prometheus.toml"
    exported_env = export_root / "metadata" / "env.json"
    exported_indexed_contexts = export_root / "metadata" / "indexed-contexts.json"
    exported_trace = export_root / "stores" / "trace" / "records.jsonl"
    exported_failures = export_root / "stores" / "failures.db"
    exported_outcomes = export_root / "stores" / "outcomes.db"
    manifest_path = export_root / "manifest.json"

    assert exported_config.read_text(encoding="utf-8") == config_path.read_text(encoding="utf-8")
    assert exported_trace.read_text(encoding="utf-8") == trace_file.read_text(encoding="utf-8")
    assert exported_failures.read_bytes() == failures_db.read_bytes()
    assert exported_outcomes.read_bytes() == outcomes_db.read_bytes()
    assert manifest_path.exists()
    assert manifest.manifest_version == EXPORT_MANIFEST_VERSION

    env_payload = json.loads(exported_env.read_text(encoding="utf-8"))
    assert env_payload == {
        "entries": [
            {"name": "AXON_CONFIG", "present": True, "source": "env"},
            {"name": "AXON_ENGINE", "present": True, "source": "env"},
            {"name": "AXON_OLLAMA_LOCAL_HOST", "present": True, "source": "env"},
            {"name": "AXON_RUNTIME_MODE", "present": True, "source": "env"},
            {"name": "AXON_VAULT", "present": True, "source": "env"},
        ]
    }
    assert "ANTHROPIC_API_KEY" not in exported_env.read_text(encoding="utf-8")

    indexed_contexts_payload = json.loads(exported_indexed_contexts.read_text(encoding="utf-8"))
    assert indexed_contexts_payload == {
        "contexts": ["personal", "career", "knowledge", "saas", "work"],
        "manifest_version": EXPORT_MANIFEST_VERSION,
    }

    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_payload == {
        "artifacts": [
            {
                "kind": "config/prometheus_toml",
                "path": "config/prometheus.toml",
                "sha256": _sha256(exported_config),
                "size_bytes": exported_config.stat().st_size,
            },
            {
                "kind": "metadata/env",
                "path": "metadata/env.json",
                "sha256": _sha256(exported_env),
                "size_bytes": exported_env.stat().st_size,
            },
            {
                "kind": "metadata/indexed_contexts",
                "path": "metadata/indexed-contexts.json",
                "sha256": _sha256(exported_indexed_contexts),
                "size_bytes": exported_indexed_contexts.stat().st_size,
            },
            {
                "kind": "store/failure",
                "path": "stores/failures.db",
                "sha256": _sha256(exported_failures),
                "size_bytes": exported_failures.stat().st_size,
            },
            {
                "kind": "store/outcome",
                "path": "stores/outcomes.db",
                "sha256": _sha256(exported_outcomes),
                "size_bytes": exported_outcomes.stat().st_size,
            },
            {
                "kind": "store/trace",
                "path": "stores/trace/records.jsonl",
                "sha256": _sha256(exported_trace),
                "size_bytes": exported_trace.stat().st_size,
            },
        ],
        "manifest_version": EXPORT_MANIFEST_VERSION,
    }


def test_export_portability_bundle_omits_missing_optional_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine_root = tmp_path / "engine"
    config_path = tmp_path / "prometheus.toml"
    export_root = tmp_path / "export"

    config_path.write_text("[runtime]\nmode = \"minimal\"\n", encoding="utf-8")
    monkeypatch.setenv("AXON_CONFIG", str(config_path))
    monkeypatch.setenv("AXON_RUNTIME_MODE", "minimal")

    export_portability_bundle(
        export_root,
        runtime=SimpleNamespace(engine_root=engine_root, data_root=engine_root / "data"),
    )

    manifest_payload = json.loads((export_root / "manifest.json").read_text(encoding="utf-8"))

    assert manifest_payload["artifacts"] == [
        {
            "kind": "config/prometheus_toml",
            "path": "config/prometheus.toml",
            "sha256": _sha256(export_root / "config" / "prometheus.toml"),
            "size_bytes": (export_root / "config" / "prometheus.toml").stat().st_size,
        },
        {
            "kind": "metadata/env",
            "path": "metadata/env.json",
            "sha256": _sha256(export_root / "metadata" / "env.json"),
            "size_bytes": (export_root / "metadata" / "env.json").stat().st_size,
        },
        {
            "kind": "metadata/indexed_contexts",
            "path": "metadata/indexed-contexts.json",
            "sha256": _sha256(export_root / "metadata" / "indexed-contexts.json"),
            "size_bytes": (export_root / "metadata" / "indexed-contexts.json").stat().st_size,
        },
    ]


def test_export_portability_bundle_writes_deterministic_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "prometheus.toml"
    config_path.write_text("[runtime]\nmode = \"full-local\"\n", encoding="utf-8")
    monkeypatch.setenv("AXON_CONFIG", str(config_path))
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path / "engine"))
    monkeypatch.setenv("AXON_RUNTIME_MODE", "full-local")

    runtime = SimpleNamespace(
        engine_root=tmp_path / "engine",
        data_root=tmp_path / "engine" / "data",
    )

    export_portability_bundle(tmp_path / "export-a", runtime=runtime)
    export_portability_bundle(tmp_path / "export-b", runtime=runtime)

    assert (tmp_path / "export-a" / "manifest.json").read_text(encoding="utf-8") == (
        tmp_path / "export-b" / "manifest.json"
    ).read_text(encoding="utf-8")
    assert (tmp_path / "export-a" / "metadata" / "env.json").read_text(encoding="utf-8") == (
        tmp_path / "export-b" / "metadata" / "env.json"
    ).read_text(encoding="utf-8")
