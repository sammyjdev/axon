from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from axon.portability.exporter import export_portability_bundle
from axon.portability.importer import import_portability_bundle


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_import_portability_bundle_restores_bundle_into_fresh_engine_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_engine_root = tmp_path / "source-engine"
    source_data_root = source_engine_root / "data"
    source_trace = source_data_root / "trace" / "records.jsonl"
    source_failures = source_data_root / "failures.db"
    source_outcomes = source_data_root / "outcomes.db"
    config_path = tmp_path / "prometheus.toml"
    export_root = tmp_path / "bundle"
    imported_engine_root = tmp_path / "imported-engine"

    source_trace.parent.mkdir(parents=True, exist_ok=True)
    source_trace.write_text('{"trace_id":"trace-1"}\n', encoding="utf-8")
    source_failures.write_bytes(b"failures-db")
    source_outcomes.write_bytes(b"outcomes-db")
    config_path.write_text("[runtime]\nmode = \"hybrid-local\"\n", encoding="utf-8")

    monkeypatch.setenv("AXON_CONFIG", str(config_path))
    monkeypatch.setenv("AXON_ENGINE", str(source_engine_root))
    monkeypatch.setenv("AXON_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("AXON_RUNTIME_MODE", "hybrid-local")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-should-not-export")

    export_portability_bundle(
        export_root,
        runtime=SimpleNamespace(engine_root=source_engine_root, data_root=source_data_root),
    )

    manifest = import_portability_bundle(export_root, imported_engine_root)

    imported_config = imported_engine_root / "config" / "prometheus.toml"
    imported_env = imported_engine_root / "metadata" / "env.json"
    imported_indexed_contexts = imported_engine_root / "metadata" / "indexed-contexts.json"
    imported_trace = imported_engine_root / "data" / "trace" / "records.jsonl"
    imported_failures = imported_engine_root / "data" / "failures.db"
    imported_outcomes = imported_engine_root / "data" / "outcomes.db"

    assert manifest.manifest_version == "1"
    assert imported_config.read_text(encoding="utf-8") == config_path.read_text(encoding="utf-8")
    assert imported_trace.read_text(encoding="utf-8") == source_trace.read_text(encoding="utf-8")
    assert imported_failures.read_bytes() == source_failures.read_bytes()
    assert imported_outcomes.read_bytes() == source_outcomes.read_bytes()
    assert imported_env.read_text(encoding="utf-8") == (
        export_root / "metadata" / "env.json"
    ).read_text(encoding="utf-8")
    assert imported_indexed_contexts.read_text(encoding="utf-8") == (
        export_root / "metadata" / "indexed-contexts.json"
    ).read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in imported_env.read_text(encoding="utf-8")
    assert _sha256(imported_config) == _sha256(config_path)


def test_import_portability_bundle_rejects_wrong_manifest_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_engine_root = tmp_path / "engine"
    source_data_root = source_engine_root / "data"
    config_path = tmp_path / "prometheus.toml"
    export_root = tmp_path / "bundle"

    config_path.write_text("[runtime]\nmode = \"minimal\"\n", encoding="utf-8")
    monkeypatch.setenv("AXON_CONFIG", str(config_path))
    monkeypatch.setenv("AXON_ENGINE", str(source_engine_root))
    monkeypatch.setenv("AXON_VAULT", str(tmp_path / "vault"))

    export_portability_bundle(
        export_root,
        runtime=SimpleNamespace(engine_root=source_engine_root, data_root=source_data_root),
    )

    manifest_path = export_root / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["manifest_version"] = "999"
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    try:
        import_portability_bundle(export_root, tmp_path / "dest")
    except ValueError as exc:
        assert "Unsupported portability manifest version" in str(exc)
    else:
        raise AssertionError("expected ValueError")
