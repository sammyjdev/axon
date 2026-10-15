"""Tests for axon.hooks.precommit_integration (dec-113)."""

from __future__ import annotations

from pathlib import Path

from axon.hooks.precommit_integration import (
    axon_repo_entry,
    dry_run_message,
    merge_into,
)


class TestEntry:
    def test_entry_includes_all_four_hooks(self) -> None:
        entry = axon_repo_entry()
        assert "axon-post-commit" in entry
        assert "axon-pre-push" not in entry  # a push is not an AXON event
        assert "axon-post-merge" in entry
        assert "axon-post-checkout" in entry

    def test_entry_uses_local_repo(self) -> None:
        assert "repo: local" in axon_repo_entry()

    def test_entry_always_runs(self) -> None:
        assert "always_run: true" in axon_repo_entry()


class TestDryRun:
    def test_dry_run_mentions_path(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".pre-commit-config.yaml"
        msg = dry_run_message(cfg)
        assert str(cfg) in msg
        assert "--apply" in msg


class TestMergeInto:
    def test_appends_when_missing(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".pre-commit-config.yaml"
        cfg.write_text("repos:\n  - repo: existing\n    hooks: []\n")
        assert merge_into(cfg) is True
        text = cfg.read_text()
        assert "axon-post-commit" in text
        assert "repo: existing" in text  # original preserved

    def test_idempotent_when_already_present(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".pre-commit-config.yaml"
        cfg.write_text("repos:\n" + axon_repo_entry())
        assert merge_into(cfg) is False

    def test_returns_false_for_missing_file(self, tmp_path: Path) -> None:
        assert merge_into(tmp_path / "nope.yaml") is False

    def test_a_config_written_by_an_older_axon_is_left_untouched(self, tmp_path: Path) -> None:
        """Its axon-pre-push entry stays: the push event it sends does nothing now,
        and two review rounds of #225 found text edits folding its fields into
        axon-post-commit, which silently stopped commit capture."""
        old_config = "repos:\n" + axon_repo_entry() + (
            "      - id: axon-pre-push\n"
            "        name: AXON pre-push snapshot\n"
            "\n"
            "        entry: /opt/venv/bin/python -m axon.hooks.git_event push\n"
            "        stages: [pre-push]\n"
        )
        cfg = tmp_path / ".pre-commit-config.yaml"
        cfg.write_text(old_config)

        assert merge_into(cfg) is False
        assert cfg.read_text() == old_config

