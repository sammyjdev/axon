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


def test_commits_are_linked_after_the_transcript_is_imported() -> None:
    assert [name for name, _ in session_end.STEPS][:3] == [
        "digest",
        "import transcript",
        "link commits",
    ]


def test_the_session_hook_links_the_commit_the_session_made(tmp_path):
    import subprocess

    dsn = os.environ["AXON_PG_URL"]
    if "axon_tests_have_no_database" in dsn:
        pytest.skip("needs the test Postgres container")

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "a.txt").write_text("a")
    git("add", "-A")
    git("commit", "-q", "-m", "second change")
    full = git("rev-parse", "HEAD")
    line = {"sessionId": "hook-d", "cwd": str(repo), "timestamp": "2026-10-01T10:00:00Z"}
    path = tmp_path / "d.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({**line, **entry})
            for entry in (
                {"type": "user", "uuid": "u1",
                 "message": {"role": "user", "content": "commit it"}},
                {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "c1", "name": "Bash",
                     "input": {"command": "git commit -m 'second change'"}}]}},
                {"type": "user", "uuid": "u2", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "c1",
                     "content": f"[main {full[:7]}] second change\n 1 file changed\n"}]}},
            )
        )
        + "\n"
    )

    result = runner.invoke(
        app,
        ["session-hook"],
        input=json.dumps({"transcript_path": str(path), "cwd": str(repo)}),
    )
    assert result.exit_code == 0

    async def _links():
        con = await asyncpg.connect(dsn)
        try:
            return await con.fetch(
                "SELECT target_id FROM activity_evidence_links "
                "WHERE session_id='hook-d' AND target_type='commit'"
            )
        finally:
            await con.close()

    import asyncio

    assert [r["target_id"] for r in asyncio.run(_links())] == [full]


def test_the_context_file_is_refreshed_after_commits_are_linked() -> None:
    assert [name for name, _ in session_end.STEPS][:4] == [
        "digest",
        "import transcript",
        "link commits",
        "refresh context",
    ]


def test_a_session_that_ends_shows_its_first_and_last_ask_in_the_repo_context(tmp_path):
    """The adapters' message format, the import and the context query agree end to end."""
    import subprocess

    dsn = os.environ["AXON_PG_URL"]
    if "axon_tests_have_no_database" in dsn:
        pytest.skip("needs the test Postgres container")
    repo = tmp_path / "ctxrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)  # noqa: S603, S607
    base = {"sessionId": "hook-c", "cwd": str(repo)}
    path = tmp_path / "c.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({**base, **entry})
            for entry in (
                {"type": "user", "uuid": "u1", "timestamp": "2026-10-01T10:00:00Z",
                 "message": {"role": "user", "content": "add the csv export to the report"}},
                {"type": "assistant", "uuid": "a1", "timestamp": "2026-10-01T10:01:00Z",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}},
                {"type": "user", "uuid": "u2", "timestamp": "2026-10-01T10:02:00Z",
                 "message": {"role": "user", "content": "now cover it with a test"}},
            )
        )
        + "\n"
    )

    result = runner.invoke(
        app,
        ["session-hook"],
        input=json.dumps({"transcript_path": str(path), "cwd": str(repo)}),
    )

    assert result.exit_code == 0
    context = (repo / ".axon" / "context.md").read_text()
    assert "add the csv export to the report" in context
    assert "now cover it with a test" in context
