"""Task 4: `axon activity` CLI group, backed only by ActivityService.

Uses a real testcontainers Postgres (same pattern as
tests/activity/test_repository.py) with axon.cli.pb's module-level _RUNTIME
pointed at the container DSN, since RuntimeConfig is a frozen dataclass and
AXON_PG_URL is read once at import time.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("testcontainers.postgres")
import asyncpg  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.__main__ import app as cli_app  # noqa: E402
from axon.cli import pb  # noqa: E402

runner = CliRunner()


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"  # noqa: S106
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture(autouse=True)
def _point_cli_at_test_db(pg_dsn, monkeypatch):
    monkeypatch.setattr(pb, "_RUNTIME", replace(pb._RUNTIME, pg_url=pg_dsn))


@pytest.fixture(autouse=True)
async def _reset_schema(pg_dsn):
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    finally:
        await con.close()


@pytest.fixture
def isolate_spool(monkeypatch, tmp_path):
    monkeypatch.setattr("axon.activity.spool.data_root", lambda: tmp_path)
    return tmp_path


def _write_fixture_event(path: Path, *, event_id: str, session_id: str) -> None:
    event = {
        "event_id": event_id,
        "schema_version": 1,
        "harness": "claude-code",
        "source_id": f"src-{event_id}",
        "session_id": session_id,
        "occurred_at": "2026-09-15T00:00:00+00:00",
        "ingested_at": "2026-09-15T00:00:01+00:00",
        "kind": "message",
        "content": {"text": "hello"},
        "coverage": "complete-observable",
        "redactions": [],
    }
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")


def test_activity_import_then_show_returns_sanitized_event(tmp_path: Path) -> None:
    fixture = tmp_path / "events.jsonl"
    _write_fixture_event(fixture, event_id="evt-1", session_id="ses-1")

    import_result = runner.invoke(
        cli_app, ["activity", "import", "--harness", "claude-code", "--path", str(fixture)]
    )
    assert import_result.exit_code == 0, import_result.output
    imported = json.loads(import_result.output)
    assert imported["imported"] == 1
    assert "coverage_warnings" in imported

    show_result = runner.invoke(cli_app, ["activity", "show", "--session", "ses-1"])
    assert show_result.exit_code == 0, show_result.output
    events = json.loads(show_result.output)
    assert len(events) == 1
    assert events[0]["event_id"] == "evt-1"


def test_activity_export_is_versioned_jsonl(tmp_path: Path) -> None:
    fixture = tmp_path / "events.jsonl"
    _write_fixture_event(fixture, event_id="evt-2", session_id="ses-2")
    runner.invoke(
        cli_app, ["activity", "import", "--harness", "claude-code", "--path", str(fixture)]
    )

    export_result = runner.invoke(cli_app, ["activity", "export", "--session", "ses-2"])
    assert export_result.exit_code == 0, export_result.output
    lines = [line for line in export_result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    envelope = json.loads(lines[0])
    assert envelope["schema_version"] == 1


def test_activity_collect_reports_pending_spool(isolate_spool) -> None:
    import asyncio

    from axon.activity.spool import spool_event

    event = {"event_id": "evt-pending", "data": "unsent"}
    asyncio.run(spool_event(event))

    result = runner.invoke(cli_app, ["activity", "collect"])
    assert result.exit_code == 0, result.output
    health = json.loads(result.output)
    assert health["pending_count"] >= 1
    assert "compatibility_warnings" in health


def test_activity_import_unknown_harness_fails_loudly(tmp_path: Path) -> None:
    fixture = tmp_path / "events.jsonl"
    _write_fixture_event(fixture, event_id="evt-3", session_id="ses-3")

    result = runner.invoke(
        cli_app, ["activity", "import", "--harness", "bogus", "--path", str(fixture)]
    )
    assert result.exit_code != 0

    show_result = runner.invoke(cli_app, ["activity", "show", "--session", "ses-3"])
    events = json.loads(show_result.output)
    assert events == []
