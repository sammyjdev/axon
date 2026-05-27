"""Tests for L1-light / L1-full gates (dec-111)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from axon.adr.gates.l1 import extract_candidates, l1_full, l1_light


class TestExtractCandidates:
    def test_extracts_paths_with_extension(self) -> None:
        paths, _ = extract_candidates(
            "We refactor src/auth/middleware.py and tests/test_x.py"
        )
        assert "src/auth/middleware.py" in paths
        assert "tests/test_x.py" in paths

    def test_extracts_camelcase_identifiers(self) -> None:
        _, idents = extract_candidates("Use AuthMiddleware to wrap RequestContext")
        assert "AuthMiddleware" in idents
        assert "RequestContext" in idents

    def test_extracts_snake_case_identifiers(self) -> None:
        _, idents = extract_candidates(
            "Calls validate_token and refresh_session"
        )
        assert "validate_token" in idents
        assert "refresh_session" in idents

    def test_drops_short_identifiers(self) -> None:
        _, idents = extract_candidates("foo bar")
        # both < 5 chars
        assert idents == []

    def test_deduplicates(self) -> None:
        paths, idents = extract_candidates("AuthMiddleware AuthMiddleware src/x.py src/x.py")
        assert paths.count("src/x.py") == 1
        assert idents.count("AuthMiddleware") == 1


class TestL1Light:
    def test_passes_when_no_candidates(self) -> None:
        # Empty ADR text → nothing to disprove
        passed, details = l1_light("", repo_root=Path("."))
        assert passed is True

    def test_fails_when_file_missing(self) -> None:
        def fake_git(root: Path, *args: str) -> str:
            if args[0] == "cat-file":
                raise subprocess.CalledProcessError(1, "git")
            return ""

        passed, details = l1_light(
            "References src/nonexistent.py module",
            repo_root=Path("."),
            git_runner=fake_git,
        )
        assert passed is False
        assert "src/nonexistent.py" in details["missing_paths"]

    def test_fails_when_identifier_missing(self) -> None:
        def fake_git(root: Path, *args: str) -> str:
            if args[0] == "grep":
                return ""  # no match
            return ""

        passed, details = l1_light(
            "FooBarBaz is the class",
            repo_root=Path("."),
            git_runner=fake_git,
        )
        assert passed is False
        assert "FooBarBaz" in details["missing_idents"]

    def test_passes_when_all_present(self) -> None:
        def fake_git(root: Path, *args: str) -> str:
            if args[0] == "grep":
                return "HEAD:src/x.py:1: FooBarBaz here\n"
            return ""

        passed, details = l1_light(
            "FooBarBaz is the class",
            repo_root=Path("."),
            git_runner=fake_git,
        )
        assert passed is True


class TestL1Full:
    def test_stub_returns_true(self) -> None:
        passed, details = l1_full("anything", repo_root=Path("."))
        assert passed is True
        assert details.get("stub") is True
