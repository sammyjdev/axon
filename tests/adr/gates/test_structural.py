"""Tests for the structural detector (dec-111)."""

from __future__ import annotations

from pathlib import Path

from axon.adr.commit_context import CommitContext
from axon.adr.gates.structural import is_structural


def _ctx(**kwargs) -> CommitContext:  # noqa: ANN003
    defaults: dict[str, object] = dict(
        commit_hash="x", subject="", body="", diff="",
        files_changed=[], new_files=[], renames=[], deleted_files=[],
        repo_root=Path("."),
    )
    defaults.update(kwargs)
    return CommitContext(**defaults)  # type: ignore[arg-type]


class TestIsStructural:
    def test_two_renames_triggers(self) -> None:
        ctx = _ctx(
            renames=[("a/x.py", "b/x.py"), ("a/y.py", "b/y.py")],
            files_changed=["b/x.py", "b/y.py"],
        )
        assert is_structural(ctx) is True

    def test_one_rename_does_not_trigger_alone(self) -> None:
        ctx = _ctx(
            renames=[("a.py", "b.py")],
            files_changed=["b.py"],
        )
        assert is_structural(ctx) is False

    def test_three_new_files_in_new_dirs_triggers(self) -> None:
        ctx = _ctx(
            new_files=["new_pkg/a.py", "new_pkg/b.py", "new_pkg/c.py"],
            files_changed=["new_pkg/a.py", "new_pkg/b.py", "new_pkg/c.py"],
        )
        assert is_structural(ctx) is True

    def test_two_dir_moves_triggers(self) -> None:
        ctx = _ctx(
            renames=[
                ("old_dir/a.py", "new_dir/a.py"),
                ("legacy/b.py", "current/b.py"),
            ],
        )
        assert is_structural(ctx) is True

    def test_path_only_diff_triggers(self) -> None:
        # All "+" / "-" headers, no content lines
        diff = (
            "diff --git a/x.py b/x.py\n"
            "rename from old/x.py\n"
            "rename to new/x.py\n"
            "similarity index 100%\n"
        )
        ctx = _ctx(diff=diff, renames=[("old/x.py", "new/x.py")])
        # 1 rename alone doesn't trigger, but path_only_ratio is 1.0
        assert is_structural(ctx) is True

    def test_pure_content_change_does_not_trigger(self) -> None:
        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-old line\n"
            "+new line\n"
            " unchanged\n"
        )
        ctx = _ctx(diff=diff, files_changed=["x.py"])
        assert is_structural(ctx) is False
