"""Acceptance: the SessionEnd hook stores the digest, then imports the transcript."""

import json
import os
from pathlib import Path

import asyncpg
import pytest
from typer.testing import CliRunner

from axon.cli.pb import app
from axon.hooks import session_end

runner = CliRunner()


def _transcript(tmp_path: Path) -> Path:
    path = tmp_path / "s.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "uuid": "u1",
                "sessionId": "hook-1",
                "timestamp": "2026-10-01T10:00:00Z",
                "cwd": str(tmp_path),
                "message": {"role": "user", "content": "store this conversation"},
            }
        )
        + "\n"
    )
    return path


def test_session_end_runs_the_import_after_the_digest_and_a_failing_step_stops_nothing(
    tmp_path, monkeypatch
):
    calls: list[str] = []

    def boom(t, c):
        calls.append("digest")
        raise RuntimeError("store down")

    monkeypatch.setattr(
        session_end,
        "STEPS",
        [("digest", boom), ("import transcript", lambda t, c: calls.append("import"))],
    )
    path = _transcript(tmp_path)
    result = runner.invoke(
        app,
        ["session-hook"],
        input=json.dumps({"transcript_path": str(path), "cwd": str(tmp_path)}),
    )
    assert result.exit_code == 0
    assert calls == ["digest", "import"]
    assert "digest failed: store down" in result.output


def test_the_session_hook_stores_the_transcript_events(tmp_path):
    dsn = os.environ["AXON_PG_URL"]
    if "axon_tests_have_no_database" in dsn:
        pytest.skip("needs the test Postgres container")
    path = _transcript(tmp_path)

    result = runner.invoke(
        app,
        ["session-hook"],
        input=json.dumps({"transcript_path": str(path), "cwd": str(tmp_path)}),
    )
    assert result.exit_code == 0

    async def _texts():
        con = await asyncpg.connect(dsn)
        try:
            return await con.fetch(
                "SELECT content->>'text' AS t FROM activity_events WHERE session_id='hook-1'"
            )
        finally:
            await con.close()

    import asyncio

    assert [r["t"] for r in asyncio.run(_texts())] == ["store this conversation"]


def test_a_session_under_the_restricted_tree_runs_no_step(tmp_path, monkeypatch):
    from axon.activity import importer

    restricted = tmp_path / "afya"
    restricted.mkdir()
    monkeypatch.setattr(importer, "RESTRICTED_ROOT", restricted)
    calls: list[str] = []
    monkeypatch.setattr(session_end, "STEPS", [("digest", lambda t, c: calls.append("x"))])
    path = _transcript(tmp_path)
    result = runner.invoke(
        app,
        ["session-hook"],
        input=json.dumps({"transcript_path": str(path), "cwd": str(restricted)}),
    )
    assert result.exit_code == 0
    assert calls == []


def test_an_unreadable_transcript_never_fails_the_hook(tmp_path, monkeypatch):
    monkeypatch.setattr(session_end, "STEPS", [])
    path = _transcript(tmp_path)
    path.chmod(0)
    try:
        result = runner.invoke(
            app,
            ["session-hook"],
            input=json.dumps({"transcript_path": str(path), "cwd": str(tmp_path)}),
        )
    finally:
        path.chmod(0o600)
    assert result.exit_code == 0


def test_a_transcript_that_later_enters_the_restricted_tree_runs_no_step(
    tmp_path, monkeypatch
):
    from axon.activity import importer

    restricted = tmp_path / "afya"
    restricted.mkdir()
    monkeypatch.setattr(importer, "RESTRICTED_ROOT", restricted)
    calls: list[str] = []
    monkeypatch.setattr(session_end, "STEPS", [("digest", lambda t, c: calls.append("x"))])
    path = _transcript(tmp_path)
    with path.open("a") as f:
        f.write(json.dumps({"type": "user", "sessionId": "hook-1", "cwd": str(restricted)}) + "\n")
    runner.invoke(
        app, ["session-hook"],
        input=json.dumps({"transcript_path": str(path), "cwd": str(tmp_path)}),
    )
    assert calls == []


def test_a_transcript_with_invalid_utf8_never_fails_the_hook(tmp_path, monkeypatch):
    monkeypatch.setattr(session_end, "STEPS", [])
    path = tmp_path / "bad.jsonl"
    path.write_bytes(b'{"sessionId": "\xff\xfe"}\n')
    result = runner.invoke(
        app, ["session-hook"],
        input=json.dumps({"transcript_path": str(path), "cwd": str(tmp_path)}),
    )
    assert result.exit_code == 0


def test_the_transcript_is_imported_after_the_digest_is_stored() -> None:
    assert [name for name, _ in session_end.STEPS][:2] == ["digest", "import transcript"]
