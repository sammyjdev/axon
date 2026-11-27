"""``axon pending drain`` quarantines nothing it cannot keep out of git (#202)."""

from __future__ import annotations

from typer.testing import CliRunner

from axon.cli.pb import app
from axon.store.session_store import SessionStore
from tests.cli.test_spool_not_committable import _git

runner = CliRunner()


def test_drain_exits_1_when_quarantine_is_refused(tmp_path, monkeypatch):
    """Criterion 8. The store is stubbed: no Postgres, the real drain."""
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))
    _git(tmp_path, "init")
    (tmp_path / ".axon" / ".gitignore").mkdir(parents=True)
    bad = tmp_path / ".axon" / "pending" / "session-bad.json"
    bad.parent.mkdir()
    bad.write_text("{not json", encoding="utf-8")

    class _Store(SessionStore):
        async def init(self):
            return None

        async def close(self):
            return None

    monkeypatch.setattr("axon.store.session_store.SessionStore", _Store)

    result = runner.invoke(app, ["pending", "drain"])

    assert result.exit_code == 1
    assert [ln for ln in result.stderr.splitlines() if ln.startswith("spool not ignored:")]
    assert bad.exists(), "the malformed payload was moved"
    assert list((tmp_path / ".axon").glob("pending-quarantine/*")) == []
