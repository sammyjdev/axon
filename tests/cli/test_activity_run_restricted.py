"""`axon activity run --harness agy` skips a workspace under the restricted tree.

The Claude Code and Codex importers skip a session whose cwd is under
AXON_RESTRICTED_ROOT; the agy path must not be the way around that gate.
"""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from axon.activity import importer
from axon.cli.pb import app

runner = CliRunner()


def test_an_agy_run_in_the_restricted_tree_is_skipped_and_nothing_is_stored(
    monkeypatch, tmp_path: Path
) -> None:
    restricted = tmp_path / "employer"
    workspace = restricted / "repo"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(importer, "RESTRICTED_ROOT", restricted)

    def _must_not_run(**_kwargs):
        raise AssertionError("agy ran in the restricted tree")

    def _must_not_store(*_args, **_kwargs):
        raise AssertionError("the restricted session reached the activity store")

    monkeypatch.setattr("axon.activity.agy_runner.run_agy", _must_not_run)
    monkeypatch.setattr(
        "axon.activity.repository.PostgresActivityRepository", _must_not_store
    )

    result = runner.invoke(
        app, ["activity", "run", "--harness", "agy", "--workspace", str(workspace), "hi"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"skipped": "restricted cwd"}
