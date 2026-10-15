"""Tests for chunk id derivation with repo scoping."""

from __future__ import annotations

import uuid

from axon.embedder.pipeline import _chunk_id


def test_the_same_relative_path_in_two_repos_gets_different_ids() -> None:
    """The same relative path in different repos produces distinct chunk IDs."""
    id_a = _chunk_id("README.md", "sym", 0, repo="repo_a")
    id_b = _chunk_id("README.md", "sym", 0, repo="repo_b")
    assert id_a != id_b


def test_the_same_identity_gets_the_same_id() -> None:
    """Calls with the same repo and relative path produce identical chunk IDs."""
    id_1 = _chunk_id("README.md", "sym", 0, repo="repo_a")
    id_2 = _chunk_id("README.md", "sym", 0, repo="repo_a")
    assert id_1 == id_2


def test_the_unscoped_key_is_todays_path_verbatim() -> None:
    """Unscoped chunk ID uses the legacy path verbatim with empty repo field."""
    expected = str(uuid.uuid5(uuid.NAMESPACE_URL, "::/abs/x.py::sym::0"))
    assert _chunk_id("/abs/x.py", "sym", 0) == expected
