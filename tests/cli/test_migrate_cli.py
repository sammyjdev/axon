from __future__ import annotations

from typer.testing import CliRunner

from axon.cli.pb import app


def test_migrate_decisions_dry_run_invokes_backfill(monkeypatch):
    captured = {}

    async def fake_run_backfill(sqlite_path, pg_dsn, *, dry_run=False):
        captured["args"] = (sqlite_path, pg_dsn, dry_run)
        from axon.store.decision_backfill import BackfillReport
        return BackfillReport(
            copied_decisions=110, renumbered=(("dec-001", "dec-111"),),
            skipped_dup=(), copied_adrs=33, dry_run=dry_run,
        )

    monkeypatch.setattr("axon.store.decision_backfill.run_backfill", fake_run_backfill)
    result = CliRunner().invoke(
        app, ["migrate", "decisions-sqlite-to-pg", "--dry-run", "--sqlite", "/tmp/x.db"]
    )
    assert result.exit_code == 0, result.output
    assert captured["args"][0] == "/tmp/x.db"
    assert captured["args"][2] is True  # dry_run threaded through
    assert "110" in result.output and "dec-001" in result.output and "dec-111" in result.output
