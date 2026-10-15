"""Tests for axon.hooks.precommit_integration (dec-113)."""

from __future__ import annotations

from pathlib import Path

import pytest

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

    def test_reinstalling_drops_the_retired_pre_push_entry_and_keeps_the_rest(
        self, tmp_path: Path
    ) -> None:
        """A config written by an older AXON already holds axon-post-commit, so
        the append-if-missing path returned before the pre-push entry was gone."""
        old_pre_push = (
            "      - id: axon-pre-push\n"
            "        name: AXON pre-push snapshot\n"
            "        entry: /opt/venv/bin/python -m axon.hooks.git_event push\n"
            "        language: system\n"
            "        stages: [pre-push]\n"
            "        always_run: true\n"
            "        pass_filenames: false\n"
        )
        entry = axon_repo_entry()
        marker = "      - id: axon-post-merge\n"
        old_config = "repos:\n" + entry.replace(marker, old_pre_push + marker)
        cfg = tmp_path / ".pre-commit-config.yaml"
        cfg.write_text(old_config)

        changed = merge_into(cfg)

        assert changed is True
        assert cfg.read_text() == "repos:\n" + entry
        assert merge_into(cfg) is False

    @pytest.mark.parametrize("separator", ["\n", "# kept by hand\n", "    # indented note\n"])
    def test_a_blank_or_comment_line_inside_the_retired_entry_does_not_leak_its_fields(
        self, tmp_path: Path, separator: str
    ) -> None:
        """Review of #225, rounds 2 and 3: a blank line, then a comment, between
        `name` and `entry` ended the removal, and `entry: ... push` and
        `stages: [pre-push]` fell under the hook above, turning axon-post-commit
        into a push hook."""
        old_pre_push = (
            "      - id: axon-pre-push\n"
            "        name: AXON pre-push snapshot\n"
            + separator
            + "        entry: /opt/venv/bin/python -m axon.hooks.git_event push\n"
            "        language: system\n"
            "        stages: [pre-push]\n"
            "        always_run: true\n"
            "        pass_filenames: false\n"
        )
        entry = axon_repo_entry()
        marker = "      - id: axon-post-merge\n"
        cfg = tmp_path / ".pre-commit-config.yaml"
        cfg.write_text("repos:\n" + entry.replace(marker, old_pre_push + marker))

        merge_into(cfg)

        assert cfg.read_text() == "repos:\n" + entry
