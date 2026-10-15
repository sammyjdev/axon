"""Tests for display_path in axon.core.file_identity."""

from __future__ import annotations

from pathlib import Path

from axon.core.file_identity import (
    RepoRoots,
    build_repo_roots,
    display_path,
)


def _roots() -> RepoRoots:
    return build_repo_roots(
        [
            Path("/w/lume"),
            Path("/w/axon"),
        ],
        Path("/v"),
    )


def test_display_path_resolved_when_repo_has_root() -> None:
    """A relative path under a known repo resolves to its absolute posix path."""
    roots = _roots()
    assert display_path("src/a.py", "lume", roots) == "/w/lume/src/a.py"


def test_display_path_marked_not_on_this_machine_when_repo_unknown() -> None:
    """A relative path whose repo has no root on this machine is marked accordingly."""
    roots = _roots()
    assert (
        display_path("src/x.py", "pharos", roots)
        == "pharos:src/x.py (not on this machine)"
    )


def test_display_path_legacy_absolute_path_rendered_verbatim() -> None:
    """An absolute path renders verbatim even if its project has a root on this machine."""
    roots = _roots()
    assert (
        display_path("/Users/sam/dev/axon/src/x.py", "axon", roots)
        == "/Users/sam/dev/axon/src/x.py"
    )


def test_display_path_empty_project_rendered_verbatim() -> None:
    """An empty project string causes the file_path to be returned verbatim."""
    roots = _roots()
    assert display_path("/tmp/a.py", "", roots) == "/tmp/a.py"  # noqa: S108
    assert display_path("relative/path.py", "", roots) == "relative/path.py"
