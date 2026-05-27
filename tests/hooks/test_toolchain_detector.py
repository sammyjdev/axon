"""Tests for axon.hooks.toolchain_detector (dec-113)."""

from __future__ import annotations

import json
from pathlib import Path

from axon.hooks.toolchain_detector import Toolchain, detect


def _init_git(root: Path) -> None:
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".git" / "hooks").mkdir(parents=True, exist_ok=True)


class TestDetect:
    def test_pre_commit_yaml_detected(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
        assert detect(tmp_path) == Toolchain.PRE_COMMIT_FRAMEWORK

    def test_pre_commit_yml_detected(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        (tmp_path / ".pre-commit-config.yml").write_text("repos: []\n")
        assert detect(tmp_path) == Toolchain.PRE_COMMIT_FRAMEWORK

    def test_husky_dir_detected(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        (tmp_path / ".husky").mkdir()
        assert detect(tmp_path) == Toolchain.HUSKY

    def test_husky_in_package_json_detected(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        (tmp_path / "package.json").write_text(
            json.dumps({"husky": {"hooks": {}}})
        )
        assert detect(tmp_path) == Toolchain.HUSKY

    def test_custom_hooks_detected(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        (tmp_path / ".git" / "hooks" / "post-commit").write_text(
            "#!/bin/sh\necho custom\n"
        )
        assert detect(tmp_path) == Toolchain.CUSTOM

    def test_axon_managed_hook_does_not_count_as_custom(
        self, tmp_path: Path
    ) -> None:
        _init_git(tmp_path)
        (tmp_path / ".git" / "hooks" / "post-commit").write_text(
            "#!/bin/sh\n# AXON git hook\necho ok\n"
        )
        assert detect(tmp_path) == Toolchain.NONE

    def test_sample_files_do_not_count_as_custom(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        (tmp_path / ".git" / "hooks" / "pre-commit.sample").write_text("# sample")
        assert detect(tmp_path) == Toolchain.NONE

    def test_no_toolchain_returns_none(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        assert detect(tmp_path) == Toolchain.NONE

    def test_pre_commit_wins_over_husky(self, tmp_path: Path) -> None:
        _init_git(tmp_path)
        (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n")
        (tmp_path / ".husky").mkdir()
        assert detect(tmp_path) == Toolchain.PRE_COMMIT_FRAMEWORK
