"""``axon doctor`` shows spool refusals (#202)."""

from __future__ import annotations

import json
from pathlib import Path

from axon.config.runtime import load_runtime_config
from axon.doctor import run_all_checks
from axon.doctor.formatters.human import format_results as human_format

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
