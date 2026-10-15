"""Tests for load_repo_roots in axon.core.file_identity."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from axon.core.file_identity import load_repo_roots


def test_load_repo_roots_reads_the_registry_and_adds_the_vault(tmp_path: Path) -> None:
    """Reads onboarded_repos.json from data_root and includes vault from vault_root."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    registry_file = data_dir / "onboarded_repos.json"
    registry_file.write_text(
        json.dumps(["/repos/alpha", "/repos/beta"]),
        encoding="utf-8",
    )
    vault_dir = tmp_path / "vault"

    runtime = SimpleNamespace(data_root=data_dir, vault_root=vault_dir)
    roots = load_repo_roots(runtime)

    assert roots["alpha"] == Path("/repos/alpha")
    assert roots["beta"] == Path("/repos/beta")
    assert roots["vault"] == vault_dir


def test_load_repo_roots_is_inert_when_the_registry_is_absent(tmp_path: Path) -> None:
    """Returns only vault root when onboarded_repos.json does not exist."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    vault_dir = tmp_path / "vault"

    runtime = SimpleNamespace(data_root=data_dir, vault_root=vault_dir)
    roots = load_repo_roots(runtime)

    assert roots == {"vault": vault_dir}


def test_load_repo_roots_survives_a_corrupt_registry(tmp_path: Path) -> None:
    """Tolerates invalid JSON in onboarded_repos.json and returns only vault."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    registry_file = data_dir / "onboarded_repos.json"
    registry_file.write_text("{corrupt: json content", encoding="utf-8")
    vault_dir = tmp_path / "vault"

    runtime = SimpleNamespace(data_root=data_dir, vault_root=vault_dir)
    roots = load_repo_roots(runtime)

    assert roots == {"vault": vault_dir}
