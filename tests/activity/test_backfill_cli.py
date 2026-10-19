"""Acceptance: `axon activity backfill` and `axon activity search` (phase B)."""

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from axon.cli.pb import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _needs_pg():
    if "axon_tests_have_no_database" in os.environ["AXON_PG_URL"]:
        pytest.skip("needs the test Postgres container")


def _trees(tmp_path: Path) -> tuple[Path, Path]:
    claude = tmp_path / "claude"
    sub = claude / "proj" / "s1" / "subagents"
    sub.mkdir(parents=True)
    base = {"timestamp": "2026-10-01T10:00:00Z", "cwd": "/nonexistent/p", "sessionId": "s1"}
    (claude / "proj" / "s1.jsonl").write_text(
        json.dumps({**base, "type": "user", "uuid": "u1",
                    "message": {"role": "user", "content": "rename the vault folder"}}) + "\n"
    )
    (sub / "agent-a.jsonl").write_text(
        json.dumps({**base, "type": "user", "uuid": "u2", "isSidechain": True, "agentId": "a",
                    "message": {"role": "user", "content": "GH_TOKEN=abc123 review it"}}) + "\n"
    )
    codex = tmp_path / "codex"
    codex.mkdir()
    (codex / "r.jsonl").write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "c1", "cwd": "/nonexistent/p"}})
        + "\n"
        + json.dumps({"type": "response_item", "timestamp": "2026-10-02T10:00:00Z",
                      "payload": {"type": "message", "role": "user",
                                  "content": [{"type": "input_text", "text": "vault backup"}]}})
        + "\n"
    )
    return claude, codex


def _backfill(claude: Path, codex: Path, *extra: str) -> str:
    result = runner.invoke(
        app,
        ["activity", "backfill", "--claude-dir", str(claude), "--codex-dir", str(codex), *extra],
    )
    assert result.exit_code == 0, result.output
    return result.output


def test_backfill_dry_run_counts_per_harness_and_writes_nothing(tmp_path):
    claude, codex = _trees(tmp_path)
    out = _backfill(claude, codex, "--dry-run")
    assert "[dry-run] claude-code: files=2, sessions=2, events=2, redactions=1, skipped=0" in out
    assert "[dry-run] codex: files=1, sessions=1, events=1, redactions=0, skipped=0" in out
    search = runner.invoke(app, ["activity", "search", "vault"])
    assert "no conversation matching 'vault'." in search.output


def test_backfill_imports_everything_once_and_search_finds_it_newest_first(tmp_path):
    claude, codex = _trees(tmp_path)
    assert "claude-code: files=2, sessions=2, events=2" in _backfill(claude, codex)
    assert "claude-code: files=2, sessions=0, events=0" in _backfill(claude, codex)

    out = runner.invoke(app, ["activity", "search", "vault"]).output.splitlines()
    assert out == [
        "2026-10-02 10:00 c1 [-/codex] user: vault backup",
        "2026-10-01 10:00 s1 [-/claude-code] user: rename the vault folder",
    ]
    redacted = runner.invoke(app, ["activity", "search", "review it"]).output
    assert "GH_TOKEN=[REDACTED:credential] review it" in redacted
    assert "abc123" not in redacted


def test_the_mcp_tool_returns_the_same_hits_as_the_cli(tmp_path):
    import asyncio

    from axon.mcp.server import axon_search_conversations

    claude, codex = _trees(tmp_path)
    _backfill(claude, codex)
    out = asyncio.run(axon_search_conversations("vault", harness="codex"))
    assert out == "2026-10-02 10:00 c1 [-/codex] user: vault backup"



def test_backfill_dry_run_against_an_empty_database_creates_no_table(tmp_path, monkeypatch):
    import asyncio

    import asyncpg

    dsn = os.environ["AXON_PG_URL"]
    empty = dsn.rsplit("/", 1)[0] + "/dryrun_empty"

    async def _admin(sql: str):
        con = await asyncpg.connect(dsn)
        try:
            await con.execute(sql)
        finally:
            await con.close()

    async def _tables() -> int:
        con = await asyncpg.connect(empty)
        try:
            return await con.fetchval(
                "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
            )
        finally:
            await con.close()

    asyncio.run(_admin("DROP DATABASE IF EXISTS dryrun_empty"))
    asyncio.run(_admin("CREATE DATABASE dryrun_empty"))
    try:
        monkeypatch.setenv("AXON_PG_URL", empty)
        claude, codex = _trees(tmp_path)
        assert "[dry-run] claude-code: files=2" in _backfill(claude, codex, "--dry-run")
        assert asyncio.run(_tables()) == 0
    finally:
        asyncio.run(_admin("DROP DATABASE IF EXISTS dryrun_empty"))
