from __future__ import annotations

from pathlib import Path

import pytest

from prometheus.config.runtime import load_runtime_config
from prometheus.expansion.service import ExpansionService
from prometheus.expansion.staging import load_draft, render_draft


def test_approve_recomputes_publish_path_from_ctx_and_topic(monkeypatch, tmp_path: Path) -> None:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(engine_root))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(vault_root))

    knowledge_root = vault_root / "knowledge"
    knowledge_root.mkdir(parents=True, exist_ok=True)
    (knowledge_root / "howto.md").write_text(
        "# Vector Search\ncreated: 2026-04-23\n", encoding="utf-8"
    )

    runtime = load_runtime_config()
    service = ExpansionService(runtime)
    staging_path = service.run(ctx="knowledge", topic="vector search", fast=True, allow_cloud=False)

    draft = load_draft(staging_path)
    tampered = draft.to_payload()
    external_target = tmp_path / "outside.md"
    tampered["publish_path"] = str(external_target)
    staging_path.write_text(
        render_draft(draft.from_payload(tampered, draft.body)),
        encoding="utf-8",
    )

    publish_path, _ = service.approve(staging_path)

    assert publish_path == vault_root / "knowledge" / "expansion" / "vector-search.md"
    assert publish_path.exists()
    assert not external_target.exists()


def test_approve_rejects_when_ctx_metadata_differs_from_staging_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(engine_root))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(vault_root))

    knowledge_root = vault_root / "knowledge"
    knowledge_root.mkdir(parents=True, exist_ok=True)
    (knowledge_root / "note.md").write_text("# Note\ncreated: 2026-04-23\n", encoding="utf-8")

    runtime = load_runtime_config()
    service = ExpansionService(runtime)
    staging_path = service.run(
        ctx="knowledge", topic="secure publish", fast=True, allow_cloud=False
    )

    draft = load_draft(staging_path)
    tampered = draft.to_payload()
    tampered["ctx"] = "work"
    staging_path.write_text(
        render_draft(draft.from_payload(tampered, draft.body)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ctx do staging diverge do diretorio"):
        service.approve(staging_path)


def test_approve_rejects_when_ctx_metadata_is_path_traversal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(engine_root))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(vault_root))

    knowledge_root = vault_root / "knowledge"
    knowledge_root.mkdir(parents=True, exist_ok=True)
    (knowledge_root / "note.md").write_text("# Note\ncreated: 2026-04-23\n", encoding="utf-8")

    runtime = load_runtime_config()
    service = ExpansionService(runtime)
    staging_path = service.run(
        ctx="knowledge", topic="secure publish 2", fast=True, allow_cloud=False
    )

    draft = load_draft(staging_path)
    tampered = draft.to_payload()
    tampered["ctx"] = "../../tmp"
    staging_path.write_text(
        render_draft(draft.from_payload(tampered, draft.body)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ctx do staging diverge do diretorio"):
        service.approve(staging_path)


def test_review_and_approve_reject_files_inside_staging_rejected(
    monkeypatch,
    tmp_path: Path,
) -> None:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(engine_root))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(vault_root))

    knowledge_root = vault_root / "knowledge"
    knowledge_root.mkdir(parents=True, exist_ok=True)
    (knowledge_root / "note.md").write_text("# Note\ncreated: 2026-04-23\n", encoding="utf-8")

    runtime = load_runtime_config()
    service = ExpansionService(runtime)
    staging_path = service.run(
        ctx="knowledge", topic="secure rejected path", fast=True, allow_cloud=False
    )

    rejected_path = knowledge_root / "staging" / "rejected" / staging_path.name
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    rejected_path.write_text(staging_path.read_text(encoding="utf-8"), encoding="utf-8")
    staging_path.unlink()

    with pytest.raises(ValueError, match="arquivo fora da area de staging"):
        service.review(rejected_path)

    with pytest.raises(ValueError, match="arquivo fora da area de staging"):
        service.approve(rejected_path)
