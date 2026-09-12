from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from axon.cli.pb import app
from tests.cli.test_spool_not_committable import _git


def test_recover_ignores_spool_before_the_first_payload_move(tmp_path, monkeypatch):
    repo = tmp_path
    root = repo / ".axon"
    quarantine = root / "pending-quarantine"
    monkeypatch.setenv("AXON_DATA_ROOT", str(root))
    _git(repo, "init")
    quarantine.mkdir(parents=True)
    (quarantine / "session.json.123").write_text(
        json.dumps({"kind": "session_memory", "summary": "literal user prompt"}),
        encoding="utf-8",
    )

    real_replace = os.replace
    payload_move_seen = False

    def replace_after_ignore(src, dst):
        nonlocal payload_move_seen
        if Path(src).parent == quarantine:
            payload_move_seen = True
            ignored = subprocess.run(  # noqa: S603
                ["git", "-C", str(repo), "check-ignore", "-q", ".axon/pending/probe"],  # noqa: S603, S607
                capture_output=True,
            )
            assert ignored.returncode == 0, "recover moved a payload before ignoring the spool"
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace_after_ignore)

    result = CliRunner().invoke(app, ["pending", "recover"])

    assert result.exit_code == 0, result.exception
    assert payload_move_seen, "recover did not exercise the observed payload move"
