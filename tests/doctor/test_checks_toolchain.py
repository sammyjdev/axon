"""Tests for axon.doctor.checks.toolchain (dec-114)."""

from __future__ import annotations

import json
from pathlib import Path

from axon.doctor import CheckStatus
from axon.doctor.checks.toolchain import check_commitlint_compat


class TestCommitlintCompat:
    def test_no_config_returns_ok(self, tmp_path: Path) -> None:
        result = check_commitlint_compat(tmp_path)
        assert result.status is CheckStatus.OK

    def test_type_enum_with_arch_returns_ok(self, tmp_path: Path) -> None:
        cfg = tmp_path / "commitlint.config.js"
        cfg.write_text(
            "module.exports = { rules: { 'type-enum': "
            "[2, 'always', ['feat', 'fix', 'arch', 'decision']] } };\n"
        )
        result = check_commitlint_compat(tmp_path)
        assert result.status is CheckStatus.OK

    def test_type_enum_without_arch_warns(self, tmp_path: Path) -> None:
        cfg = tmp_path / "commitlint.config.js"
        cfg.write_text(
            "module.exports = { rules: { 'type-enum': "
            "[2, 'always', ['feat', 'fix', 'chore']] } };\n"
        )
        result = check_commitlint_compat(tmp_path)
        assert result.status is CheckStatus.WARN
        assert "arch" in result.suggestion

    def test_package_json_commitlint_without_arch_warns(self, tmp_path: Path) -> None:
        pkg = tmp_path / "package.json"
        pkg.write_text(
            json.dumps(
                {
                    "commitlint": {
                        "rules": {
                            "type-enum": [2, "always", ["feat", "fix"]],
                        }
                    }
                }
            )
        )
        result = check_commitlint_compat(tmp_path)
        assert result.status is CheckStatus.WARN

    def test_package_json_commitlint_with_arch_ok(self, tmp_path: Path) -> None:
        pkg = tmp_path / "package.json"
        pkg.write_text(
            json.dumps(
                {
                    "commitlint": {
                        "rules": {
                            "type-enum": [2, "always", ["feat", "arch"]],
                        }
                    }
                }
            )
        )
        result = check_commitlint_compat(tmp_path)
        assert result.status is CheckStatus.OK

    def test_commitlintrc_without_arch_warns(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".commitlintrc.json"
        cfg.write_text(
            json.dumps(
                {"rules": {"type-enum": [2, "always", ["feat", "fix"]]}}
            )
        )
        result = check_commitlint_compat(tmp_path)
        assert result.status is CheckStatus.WARN
