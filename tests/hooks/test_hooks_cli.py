"""Smoke tests for pb hooks install (dec-113)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from axon.cli.pb import app

runner = CliRunner(mix_stderr=False)


def _init_git(root: Path) -> None:
    (root / ".git" / "hooks").mkdir(parents=True, exist_ok=True)


class TestHooksInstall:
    def test_dry_run_for_pre_commit_framework(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
        result = runner.invoke(app, ["hooks", "install", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "pre-commit framework" in result.stdout
        assert "axon-post-commit" in result.stdout
        # No mutation without --apply
        text = (tmp_path / ".pre-commit-config.yaml").read_text()
        assert "axon-post-commit" not in text

    def test_apply_in_non_tty_refuses(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
        # Force no-TTY deterministically (a real terminal leaves os.isatty(0)
        # True and the refusal path unexercised).
        with patch("os.isatty", return_value=False):
            result = runner.invoke(
                app, ["hooks", "install", "--path", str(tmp_path), "--apply"]
            )
        assert result.exit_code == 1
        assert "TTY" in result.stderr

    def test_apply_in_tty_mutates_pre_commit_config(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
        with patch("os.isatty", return_value=True):
            result = runner.invoke(
                app, ["hooks", "install", "--path", str(tmp_path), "--apply"]
            )
        assert result.exit_code == 0
        text = (tmp_path / ".pre-commit-config.yaml").read_text()
        assert "axon-post-commit" in text

    def test_husky_dry_run_shows_paste_text(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        (tmp_path / "package.json").write_text(json.dumps({"husky": {}}))
        result = runner.invoke(app, ["hooks", "install", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "husky" in result.stdout.lower()
        assert "post-commit" in result.stdout

    def test_husky_refuses_to_mutate_with_apply(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        (tmp_path / "package.json").write_text(json.dumps({"husky": {}}))
        with patch("os.isatty", return_value=True):
            result = runner.invoke(
                app, ["hooks", "install", "--path", str(tmp_path), "--apply"]
            )
        assert result.exit_code == 0
        assert "manual paste" in result.stdout.lower()
        # No .husky/ files mutated by AXON
        assert not (tmp_path / ".husky").exists()

    def test_non_git_repo_errors(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["hooks", "install", "--path", str(tmp_path)])
        assert result.exit_code == 1

    def test_status_shows_toolchain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _init_git(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["hooks", "status"])
        assert result.exit_code == 0
        assert "toolchain" in result.stdout


class TestDeprecatedAdrHook:
    def test_emits_deprecation_message(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        result = runner.invoke(app, ["adr", "hook", "--path", str(tmp_path)])
        # The deprecation message goes to stdout (typer.echo)
        assert "deprecated" in result.stdout.lower()
