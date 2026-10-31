"""`axon activity import` applies the restricted-tree gate per session.

An imported file carries events, not transcripts, so the gate works from the
session's workspace: the one the export recorded, the one already stored, or
one the operator declares. With AXON_RESTRICTED_ROOT set, a session whose
workspace nobody knows is skipped rather than assumed safe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axon.activity import importer


@pytest.fixture
def restricted(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "employer"
    root.mkdir()
    monkeypatch.setattr(importer, "RESTRICTED_ROOT", root)
    monkeypatch.setenv("AXON_RESTRICTED_ROOT", str(root))
    return root


def _reason(tmp_path: Path, *, workspaces=(), declared=None, path=None):
    return importer.import_skip_reason(
        path=path or tmp_path / "export.jsonl", workspaces=workspaces, declared=declared
    )


def test_a_session_recorded_under_the_restricted_tree_is_skipped(restricted, tmp_path):
    assert _reason(tmp_path, workspaces=[str(restricted / "repo")]) == "restricted cwd"


def test_a_session_outside_the_restricted_tree_is_imported(restricted, tmp_path):
    assert _reason(tmp_path, workspaces=[str(tmp_path / "personal")]) is None


def test_a_file_stored_under_the_restricted_tree_is_skipped(restricted, tmp_path):
    path = restricted / "export.jsonl"
    assert _reason(tmp_path, workspaces=[str(tmp_path / "personal")], path=path) == (
        "restricted cwd"
    )


def test_any_known_workspace_under_the_tree_skips_the_session(restricted, tmp_path):
    workspaces = [str(tmp_path / "personal"), str(restricted / "repo")]
    assert _reason(tmp_path, workspaces=workspaces) == "restricted cwd"


def test_with_the_gate_set_a_session_of_unknown_workspace_is_skipped(restricted, tmp_path):
    assert _reason(tmp_path, workspaces=[None]) == "unknown workspace"


def test_a_declared_workspace_outside_the_tree_lets_an_unknown_session_in(restricted, tmp_path):
    assert _reason(tmp_path, declared=str(tmp_path / "personal")) is None


def test_a_declared_workspace_inside_the_tree_is_skipped(restricted, tmp_path):
    assert _reason(tmp_path, declared=str(restricted / "repo")) == "restricted cwd"


def test_a_declared_workspace_does_not_override_a_recorded_one(restricted, tmp_path):
    reason = _reason(
        tmp_path, workspaces=[str(restricted / "repo")], declared=str(tmp_path / "personal")
    )
    assert reason == "restricted cwd"


def test_without_the_gate_set_a_session_of_unknown_workspace_is_imported(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("AXON_RESTRICTED_ROOT", raising=False)
    assert _reason(tmp_path) is None
