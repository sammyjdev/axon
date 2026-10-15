"""A root is used as a path prefix, so only an absolute root below `/` may be one.

Cross-review of slice 1: an empty `AXON_VAULT` became `Path('.')`, whose parts are `()`,
and so matched every absolute path as a vault file.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from axon.core.file_identity import absolute_for_identity, build_repo_roots, identity_for_path


@pytest.mark.parametrize("vault_root", ["", ".", "vault", "/"])
def test_a_vault_root_that_is_not_an_absolute_directory_is_not_a_root(vault_root: str) -> None:
    roots = build_repo_roots(["/home/u/dev/axon"], Path(vault_root))

    assert roots == {"axon": Path("/home/u/dev/axon")}
    assert identity_for_path("/etc/passwd", roots) is None
    assert identity_for_path("/home/u/dev/other/a.py", roots) is None


@pytest.mark.parametrize("entry", ["", "/", "dev/axon", "."])
def test_a_registry_entry_that_is_not_an_absolute_directory_is_not_a_root(entry: str) -> None:
    roots = build_repo_roots([entry], Path("/home/u/vault"))

    assert roots == {"vault": Path("/home/u/vault")}
    assert identity_for_path("/w/axon/src/x.py", roots) is None


def test_a_path_that_escapes_its_root_with_dotdot_has_no_identity() -> None:
    roots = build_repo_roots(["/w/axon"], Path("/w/vault"))

    assert identity_for_path("/w/axon/../other/x.py", roots) is None
    assert identity_for_path("/w/axon/src/../../other/x.py", roots) is None


def test_a_relative_path_with_dotdot_never_expands_outside_its_root() -> None:
    roots = build_repo_roots(["/w/axon"], Path("/w/vault"))

    assert absolute_for_identity("axon", "../../etc/passwd", roots) is None
    assert absolute_for_identity("axon", "src/../../x.py", roots) is None
    assert absolute_for_identity("axon", "src/x.py", roots) == Path("/w/axon/src/x.py")


def test_a_second_root_with_the_same_name_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="axon.core.file_identity"):
        roots = build_repo_roots(["/a/foo", "/b/foo"], Path("/v"))

    assert roots["foo"] == Path("/a/foo")
    assert "/b/foo" in caplog.text


def test_the_same_root_listed_twice_is_not_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="axon.core.file_identity"):
        build_repo_roots(["/a/foo", "/a/foo"], Path("/v"))

    assert caplog.text == ""
