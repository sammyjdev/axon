from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.expansion.helpers import (
    final_markdown_files,
    load_pb_module,
    newest_staging_file,
    staging_markdown_files,
)

runner = CliRunner()


def _require_expand_command(pb_module) -> None:
    result = runner.invoke(pb_module.app, ["expand", "--help"])
    if result.exit_code != 0:
        pytest.xfail("pb expand ainda não está integrado ao CLI principal")


def _seed_vault(vault_root: Path) -> None:
    (vault_root / "knowledge" / "howto").mkdir(parents=True, exist_ok=True)
    (vault_root / "knowledge" / "howto" / "existing.md").write_text(
        "# Existing\n",
        encoding="utf-8",
    )


def test_expand_run_writes_only_staging(monkeypatch, tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    engine_root = tmp_path / "engine"
    _seed_vault(vault_root)
    pb_module = load_pb_module(monkeypatch, engine_root=engine_root, vault_root=vault_root)
    _require_expand_command(pb_module)

    final_before = final_markdown_files(vault_root)

    result = runner.invoke(
        pb_module.app,
        ["expand", "run", "--ctx", "knowledge", "--topic", "vector search", "--fast"],
    )

    assert result.exit_code == 0
    assert staging_markdown_files(vault_root)
    assert final_markdown_files(vault_root) == final_before


def test_expand_review_shows_gate_fields(monkeypatch, tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    engine_root = tmp_path / "engine"
    _seed_vault(vault_root)
    pb_module = load_pb_module(monkeypatch, engine_root=engine_root, vault_root=vault_root)
    _require_expand_command(pb_module)

    run_result = runner.invoke(
        pb_module.app,
        ["expand", "run", "--ctx", "knowledge", "--topic", "embedding cache", "--fast"],
    )
    assert run_result.exit_code == 0

    staging_file = newest_staging_file(vault_root)
    assert staging_file is not None

    review_result = runner.invoke(pb_module.app, ["expand", "review", str(staging_file)])

    assert review_result.exit_code == 0
    assert "risk_level" in review_result.stdout
    assert "recommended_action" in review_result.stdout


def test_expand_approve_publishes_and_reindexes(monkeypatch, tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    engine_root = tmp_path / "engine"
    _seed_vault(vault_root)
    pb_module = load_pb_module(monkeypatch, engine_root=engine_root, vault_root=vault_root)
    _require_expand_command(pb_module)

    final_before = final_markdown_files(vault_root)

    run_result = runner.invoke(
        pb_module.app,
        ["expand", "run", "--ctx", "knowledge", "--topic", "qdrant filters", "--fast"],
    )
    assert run_result.exit_code == 0

    staging_file = newest_staging_file(vault_root)
    assert staging_file is not None

    approve_result = runner.invoke(pb_module.app, ["expand", "approve", str(staging_file)])

    assert approve_result.exit_code == 0
    assert final_markdown_files(vault_root) != final_before
    assert "index" in approve_result.stdout.lower() or "reindex" in approve_result.stdout.lower()


def test_expand_reject_keeps_final_vault_unchanged(monkeypatch, tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    engine_root = tmp_path / "engine"
    _seed_vault(vault_root)
    pb_module = load_pb_module(monkeypatch, engine_root=engine_root, vault_root=vault_root)
    _require_expand_command(pb_module)

    final_before = final_markdown_files(vault_root)

    run_result = runner.invoke(
        pb_module.app,
        ["expand", "run", "--ctx", "knowledge", "--topic", "tree sitter chunking", "--fast"],
    )
    assert run_result.exit_code == 0

    staging_file = newest_staging_file(vault_root)
    assert staging_file is not None

    reject_result = runner.invoke(pb_module.app, ["expand", "reject", str(staging_file)])

    assert reject_result.exit_code == 0
    assert final_markdown_files(vault_root) == final_before


def test_expand_review_rejected_path_shows_friendly_error(monkeypatch, tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    engine_root = tmp_path / "engine"
    _seed_vault(vault_root)
    pb_module = load_pb_module(monkeypatch, engine_root=engine_root, vault_root=vault_root)
    _require_expand_command(pb_module)

    run_result = runner.invoke(
        pb_module.app,
        ["expand", "run", "--ctx", "knowledge", "--topic", "review rejected path", "--fast"],
    )
    assert run_result.exit_code == 0

    staging_file = newest_staging_file(vault_root)
    assert staging_file is not None

    reject_result = runner.invoke(pb_module.app, ["expand", "reject", str(staging_file)])
    assert reject_result.exit_code == 0

    rejected_path = vault_root / "knowledge" / "staging" / "rejected" / staging_file.name
    review_result = runner.invoke(pb_module.app, ["expand", "review", str(rejected_path)])

    assert review_result.exit_code == 1
    assert "Não foi possível concluir a operação" in review_result.output
    assert "arquivo fora da area de staging" in review_result.output
    assert "Traceback" not in review_result.output


def test_expand_approve_invalid_path_shows_friendly_error(monkeypatch, tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    engine_root = tmp_path / "engine"
    _seed_vault(vault_root)
    pb_module = load_pb_module(monkeypatch, engine_root=engine_root, vault_root=vault_root)
    _require_expand_command(pb_module)

    invalid_path = vault_root / "knowledge" / "missing.md"
    approve_result = runner.invoke(pb_module.app, ["expand", "approve", str(invalid_path)])

    assert approve_result.exit_code == 1
    assert "Arquivo não encontrado" in approve_result.output
    assert str(invalid_path) in approve_result.output
    assert "Traceback" not in approve_result.output


def test_expand_reject_invalid_path_shows_friendly_error(monkeypatch, tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    engine_root = tmp_path / "engine"
    _seed_vault(vault_root)
    pb_module = load_pb_module(monkeypatch, engine_root=engine_root, vault_root=vault_root)
    _require_expand_command(pb_module)

    invalid_path = vault_root / "knowledge" / "outside-staging.md"
    invalid_path.write_text("# Draft\n", encoding="utf-8")

    reject_result = runner.invoke(pb_module.app, ["expand", "reject", str(invalid_path)])

    assert reject_result.exit_code == 1
    assert "Não foi possível concluir a operação" in reject_result.output
    assert "arquivo fora da area de staging" in reject_result.output
    assert "Traceback" not in reject_result.output
