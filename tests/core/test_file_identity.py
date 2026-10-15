"""Tests for file identity and repo roots in axon.core.file_identity."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from axon.core.file_identity import (
    RepoRoots,
    absolute_for_identity,
    build_repo_roots,
    identity_for_path,
    kind_for_path,
)


def _roots() -> RepoRoots:
    return build_repo_roots(
        [
            Path("/w/lume"),
            Path("/w/lume-old"),
            Path("/w/pharos"),
            Path("/w/pharos/pharos-backend"),
        ],
        Path("/v"),
    )


def test_absolute_path_round_trips_through_the_identity() -> None:
    """An absolute path resolves to (repo, rel_path) and back to an absolute Path."""
    r = _roots()
    assert identity_for_path("/w/lume/src/a.py", r) == ("lume", "src/a.py")
    assert absolute_for_identity("lume", "src/a.py", r) == Path("/w/lume/src/a.py")


def test_the_vault_is_one_root_with_one_identity() -> None:
    """Vault paths resolve under the single vault identity."""
    r = _roots()
    assert identity_for_path("/v/Decisions/dec-1.md", r) == ("vault", "Decisions/dec-1.md")


def test_a_root_that_is_a_prefix_of_a_sibling_name_does_not_match() -> None:
    """A root name that prefixes a sibling name does not falsely match by string prefix."""
    r = _roots()
    ident = identity_for_path("/w/lume-old/README.md", r)
    assert ident == ("lume-old", "README.md")
    assert ident is not None and ident[0] != "lume"


def test_a_nested_root_resolves_to_the_innermost_repo() -> None:
    """Nested roots resolve to the innermost repository root."""
    r = _roots()
    assert identity_for_path("/w/pharos/pharos-backend/src/x.py", r) == (
        "pharos-backend",
        "src/x.py",
    )
    assert identity_for_path("/w/pharos/README.md", r) == ("pharos", "README.md")


def test_a_path_under_no_root_returns_nothing_instead_of_raising() -> None:
    """A path outside known roots returns None without raising."""
    r = _roots()
    assert identity_for_path("/elsewhere/x.py", r) is None


def test_the_root_directory_itself_is_not_a_file_identity() -> None:
    """The root directory itself is not a file and has no relative path."""
    r = _roots()
    assert identity_for_path("/w/lume", r) is None


def test_a_relative_path_has_no_identity() -> None:
    """A relative path cannot be placed under a machine-bound root."""
    r = _roots()
    assert identity_for_path("test.py", r) is None


def test_identity_is_case_sensitive() -> None:
    """Path segments match roots case-sensitively."""
    r = _roots()
    assert identity_for_path("/w/Lume/README.md", r) is None


def test_an_unknown_repo_has_no_absolute_path_on_this_machine() -> None:
    """A repo not present in roots produces None when expanding to absolute."""
    r = _roots()
    assert absolute_for_identity("not-here", "x.md", r) is None


def test_backslash_input_normalises_to_posix() -> None:
    """Backslash paths are normalized before segment matching."""
    r = _roots()
    assert identity_for_path("/w/lume\\src\\a.py", r) == ("lume", "src/a.py")


def test_the_first_root_wins_for_a_duplicate_basename() -> None:
    """Duplicate basenames resolve in favor of the first registered root."""
    roots = build_repo_roots([Path("/a/axon"), Path("/b/axon")], Path("/v"))
    assert roots["axon"] == Path("/a/axon")


def test_neither_function_touches_the_filesystem_or_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Functions are completely pure and touch neither disk nor subprocesses."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("filesystem or subprocess touched by pure function")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    monkeypatch.setattr(Path, "resolve", _boom)
    monkeypatch.setattr(Path, "exists", _boom)
    monkeypatch.setattr(Path, "stat", _boom)
    monkeypatch.setattr(Path, "is_dir", _boom)
    monkeypatch.setattr(Path, "is_file", _boom)
    monkeypatch.setattr(os, "stat", _boom)

    roots = build_repo_roots([Path("/w/lume")], Path("/v"))
    assert roots == {"lume": Path("/w/lume"), "vault": Path("/v")}
    assert identity_for_path("/w/lume/src/a.py", roots) == ("lume", "src/a.py")
    assert absolute_for_identity("lume", "src/a.py", roots) == Path("/w/lume/src/a.py")
    assert kind_for_path("src/a.py") == "code"
