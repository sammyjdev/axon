"""``axon doctor`` shows spool refusals (#202)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from axon.__main__ import app
from axon.config.runtime import load_runtime_config
from axon.doctor import run_all_checks
from axon.doctor.formatters.human import format_results as human_format
from axon.store.pending import SpoolNotIgnoredError, _pending_paths, write_pending
from tests.cli.test_spool_not_committable import _git

runner = CliRunner()

_RECORD = {
    "count": 2,
    "last_at": "2026-11-26T12:00:00+00:00",
    "last_dir": "/repo/.axon/pending",
    "last_reason": "git does not ignore the spool directory",
}


def _write_record(root: Path, text: str = json.dumps(_RECORD)) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "spool-refusals.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_doctor_warns_on_recorded_refusals():
    """Criterion 17."""
    record = _write_record(load_runtime_config().data_root)

    out = human_format(run_all_checks()).splitlines()

    line = (
        f"[warn] {'capture.spool_refusals':<32} 2 spool refusal(s); "
        f"last {_RECORD['last_at']}: {_RECORD['last_reason']}"
    )
    assert line in out
    assert out[out.index(line) + 1] == (
        f"        → Fix the git ignore rule for the spool, then delete {record}"
    )


def test_doctor_ok_without_record():
    """Criterion 17, no record."""
    out = human_format(run_all_checks()).splitlines()

    assert f"[ ok ] {'capture.spool_refusals':<32} no spool refusals" in out


def test_doctor_reads_the_given_data_root(tmp_path):
    record = _write_record(tmp_path / "elsewhere")

    out = human_format(run_all_checks(data_root=record.parent))

    assert "2 spool refusal(s)" in out
    assert str(record) in out


@pytest.mark.parametrize("text", ["{not json", "[]", '{"count": 2}'])
async def test_corrupt_record_is_reported_and_rewritten(tmp_path, monkeypatch, text):
    record = _write_record(load_runtime_config().data_root, text)

    out = human_format(run_all_checks())

    assert f"[warn] {'capture.spool_refusals':<32} unreadable refusal record" in out
    status = runner.invoke(app, ["status", "--repo", "x"])
    assert status.output.splitlines()[0].startswith("spool refusals: unreadable (")

    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))
    _git(tmp_path, "init")
    (tmp_path / ".axon" / ".gitignore").mkdir(parents=True)
    with pytest.raises(SpoolNotIgnoredError):
        await write_pending(payload={}, commit_hash="s", paths=_pending_paths())
    assert json.loads(record.read_text(encoding="utf-8"))["count"] == 1
