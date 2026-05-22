"""Tests for Obsidian vault discovery (T5.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from axon.obsidian import discovery


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    discovery.clear_cache()


def _make_vault(path: Path) -> Path:
    (path / ".obsidian").mkdir(parents=True)
    return path


def test_discovers_vault_from_axon_vault_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _make_vault(tmp_path / "myvault")
    monkeypatch.setenv("AXON_VAULT", str(vault))
    assert discovery.discover_vault() == vault


def test_none_when_no_vault_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_VAULT", str(tmp_path / "nowhere"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")
    assert discovery.discover_vault() is None


def test_directory_without_obsidian_marker_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("AXON_VAULT", str(plain))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")
    assert discovery.discover_vault() is None


def test_result_is_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _make_vault(tmp_path / "v")
    monkeypatch.setenv("AXON_VAULT", str(vault))
    assert discovery.discover_vault() == vault

    # Even after the vault disappears, the cached value stands.
    monkeypatch.delenv("AXON_VAULT")
    assert discovery.discover_vault() == vault
    assert discovery.discover_vault(use_cache=False) != vault
