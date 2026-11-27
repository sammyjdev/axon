"""``axon status`` reports spool refusals first, even with the store down (#202)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from axon.__main__ import app
from axon.config.runtime import load_runtime_config

runner = CliRunner()


class _StoreDown:
    def __init__(self, *_a, **_kw):
        pass

    async def init(self):
        raise ConnectionRefusedError("store down")

    async def close(self):
        return None


def test_status_first_line_reports_refusals(monkeypatch):
    """Criterion 18."""
    record = load_runtime_config().data_root / "spool-refusals.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(
        json.dumps(
            {
                "count": 2,
                "last_at": "2026-11-26T12:00:00+00:00",
                "last_dir": "/repo/.axon/pending",
                "last_reason": "git not found",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("axon.store.session_store.SessionStore", _StoreDown)

    result = runner.invoke(app, ["status", "--repo", "axon"])

    assert isinstance(result.exception, ConnectionRefusedError), "setup: the store is down"
    assert result.output.splitlines()[0] == (
        "spool refusals: 2 (last 2026-11-26T12:00:00+00:00: git not found)"
    )


def test_status_reports_zero_without_record(monkeypatch):
    monkeypatch.setattr("axon.store.session_store.SessionStore", _StoreDown)

    result = runner.invoke(app, ["status", "--repo", "axon"])

    assert result.output.splitlines()[0] == "spool refusals: 0"
