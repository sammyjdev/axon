"""Acceptance: transcripts become stored sessions and events (phase B)."""

import json
import os
from pathlib import Path

import asyncpg
import pytest

from axon.activity import importer
from axon.activity.importer import import_transcript
from axon.activity.repository import PostgresActivityRepository


@pytest.fixture
async def repo():
    dsn = os.environ["AXON_PG_URL"]
    if "axon_tests_have_no_database" in dsn:
        pytest.skip("needs the test Postgres container")
    r = PostgresActivityRepository(dsn)
    await r.ensure_schema()
    async with (await r._ensure_pool()).acquire() as con:
        await con.execute(
            "TRUNCATE activity_sessions, activity_events, activity_cursors"
        )
    yield r
    await r.close()


async def _count(repo, sql: str) -> int:
    async with (await repo._ensure_pool()).acquire() as con:
        return await con.fetchval(sql)


def _claude_line(uuid: str, role: str, content, **extra) -> str:
    return json.dumps(
        {
            "type": role,
            "uuid": uuid,
            "sessionId": "cc-1",
            "timestamp": "2026-10-01T10:00:00Z",
            "cwd": "/nonexistent/proj",
            "message": {"role": role, "content": content},
            **extra,
        }
    ) + "\n"


def _codex_lines() -> str:
    lines = [
        {"type": "session_meta", "timestamp": "2026-10-01T10:00:00Z",
         "payload": {"id": "cx-1", "session_id": "cx-root", "cwd": "/nonexistent/proj"}},
        {"type": "response_item", "timestamp": "2026-10-01T10:00:01Z",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "<environment_context>x"}]}},
        {"type": "response_item", "timestamp": "2026-10-01T10:00:02Z",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "fix the cursor bug"}]}},
        {"type": "response_item", "timestamp": "2026-10-01T10:00:03Z",
         "payload": {"type": "function_call", "name": "shell", "arguments": "{}",
                     "call_id": "c1"}},
        {"type": "response_item", "timestamp": "2026-10-01T10:00:04Z",
         "payload": {"type": "function_call_output", "call_id": "c1", "output": "ok"}},
    ]
    return "".join(json.dumps(x) + "\n" for x in lines)


async def test_a_claude_transcript_is_stored_once_and_then_only_its_new_lines(
    repo, tmp_path: Path
):
    path = tmp_path / "cc-1.jsonl"
    path.write_text(
        _claude_line("u1", "user", "please fix the cursor bug")
        + _claude_line("u2", "user", "skill body", isMeta=True)
        + _claude_line("a1", "assistant", [{"type": "text", "text": "done"}])
    )

    first = await import_transcript(path, repo)
    assert (first.harness, first.sessions, first.events) == ("claude-code", 1, 3)
    async with (await repo._ensure_pool()).acquire() as con:
        rows = await con.fetch(
            "SELECT content FROM activity_events WHERE kind='message' ORDER BY turn_id"
        )
    by_text = {r["content"]["text"]: r["content"] for r in rows}
    assert by_text["please fix the cursor bug"]["role"] == "user"
    assert by_text["please fix the cursor bug"]["injected"] is False
    assert by_text["skill body"]["injected"] is True
    assert by_text["done"]["role"] == "assistant"

    again = await import_transcript(path, repo)
    assert again.events == 0
    assert await _count(repo, "SELECT count(*) FROM activity_events") == 3

    with path.open("a") as f:
        f.write(_claude_line("u3", "user", "one more thing"))
    third = await import_transcript(path, repo)
    assert third.events == 1
    assert await _count(repo, "SELECT count(*) FROM activity_events") == 4


async def test_a_codex_rollout_is_detected_by_content_and_normalised(repo, tmp_path: Path):
    path = tmp_path / "no-telltale-name.jsonl"
    path.write_text(_codex_lines())

    res = await import_transcript(path, repo)
    assert (res.harness, res.sessions, res.events) == ("codex", 1, 4)
    async with (await repo._ensure_pool()).acquire() as con:
        msgs = await con.fetch(
            "SELECT session_id, content FROM activity_events WHERE kind='message' "
            "ORDER BY occurred_at"
        )
    assert [m["content"] for m in msgs] == [
        {"role": "user", "text": "<environment_context>x", "injected": True},
        {"role": "user", "text": "fix the cursor bug", "injected": False},
    ]
    assert {m["session_id"] for m in msgs} == {"cx-1"}

    assert (await import_transcript(path, repo)).events == 0


async def test_a_claude_subagent_transcript_is_its_own_session_under_its_parent(
    repo, tmp_path: Path
):
    path = tmp_path / "agent-a1.jsonl"
    path.write_text(_claude_line("u1", "user", "review this", isSidechain=True, agentId="a1"))

    await import_transcript(path, repo)
    async with (await repo._ensure_pool()).acquire() as con:
        row = await con.fetchrow("SELECT session_id, parent_session_id FROM activity_sessions")
    assert (row["session_id"], row["parent_session_id"]) == ("cc-1:a1", "cc-1")


async def test_a_transcript_under_the_restricted_tree_is_skipped_unread(
    repo, tmp_path: Path, monkeypatch
):
    restricted = tmp_path / "afya"
    (restricted / "x").mkdir(parents=True)
    monkeypatch.setattr(importer, "RESTRICTED_ROOT", restricted)
    path = tmp_path / "s.jsonl"
    line = json.loads(_claude_line("u1", "user", "secret work"))
    line["cwd"] = str(restricted / "x")
    path.write_text(json.dumps(line) + "\n")

    res = await import_transcript(path, repo)
    assert res.skipped == "restricted cwd"
    assert await _count(repo, "SELECT count(*) FROM activity_events") == 0


async def test_a_database_outage_loses_nothing_the_next_import_stores_it(
    repo, tmp_path: Path
):
    path = tmp_path / "cc-1.jsonl"
    path.write_text(_claude_line("u1", "user", "hello"))
    down = PostgresActivityRepository("postgresql://x:x@127.0.0.1:1/none")
    with pytest.raises((OSError, asyncpg.PostgresError)):
        await import_transcript(path, down)

    assert (await import_transcript(path, repo)).events == 1


async def test_search_returns_the_matching_message_newest_first_without_injected_text(
    repo, tmp_path: Path
):
    path = tmp_path / "cc-1.jsonl"
    path.write_text(
        _claude_line("u1", "user", "how do we migrate the cursor table?")
        + _claude_line("u2", "user", "cursor reminder", isMeta=True)
    )
    await import_transcript(path, repo)

    hits = await repo.search_messages("CURSOR")
    assert [(h["session_id"], h["role"], h["text"]) for h in hits] == [
        ("cc-1", "user", "how do we migrate the cursor table?")
    ]
    assert await repo.search_messages("100%") == []


async def test_lines_appended_to_a_codex_rollout_are_imported_on_the_next_run(
    repo, tmp_path: Path
):
    path = tmp_path / "r.jsonl"
    path.write_text(_codex_lines())
    await import_transcript(path, repo)
    with path.open("a") as f:
        f.write(json.dumps({"type": "response_item", "timestamp": "2026-10-01T11:00:00Z",
                            "payload": {"type": "message", "role": "user",
                                        "content": [{"type": "input_text", "text": "again"}]}})
                + "\n")
    assert (await import_transcript(path, repo)).events == 1
    async with (await repo._ensure_pool()).acquire() as con:
        sid = await con.fetchval(
            "SELECT session_id FROM activity_events WHERE content->>'text'='again'"
        )
    assert sid == "cx-1"


async def test_a_claude_line_with_null_content_still_has_role_text_and_injected(
    repo, tmp_path: Path
):
    path = tmp_path / "cc.jsonl"
    path.write_text(_claude_line("u1", "user", None))
    await import_transcript(path, repo)
    async with (await repo._ensure_pool()).acquire() as con:
        content = await con.fetchval("SELECT content FROM activity_events")
    assert content == {"role": "user", "text": "", "injected": False}


async def test_a_rollout_that_moves_into_the_restricted_tree_is_skipped(
    repo, tmp_path: Path, monkeypatch
):
    restricted = tmp_path / "afya"
    restricted.mkdir()
    monkeypatch.setattr(importer, "RESTRICTED_ROOT", restricted)
    path = tmp_path / "r.jsonl"
    path.write_text(
        _codex_lines()
        + json.dumps({"type": "turn_context", "payload": {"cwd": str(restricted)}}) + "\n"
    )
    res = await import_transcript(path, repo)
    assert res.skipped == "restricted cwd"
    assert await _count(repo, "SELECT count(*) FROM activity_events") == 0


async def test_a_dotted_path_into_the_restricted_tree_is_still_skipped(
    repo, tmp_path: Path, monkeypatch
):
    restricted = tmp_path / "afya"
    (tmp_path / "other").mkdir()
    restricted.mkdir()
    monkeypatch.setattr(importer, "RESTRICTED_ROOT", restricted)
    path = tmp_path / "r.jsonl"
    path.write_text(
        _codex_lines()
        + json.dumps({"type": "turn_context",
                      "payload": {"cwd": str(tmp_path / "other" / ".." / "afya")}}) + "\n"
    )
    assert (await import_transcript(path, repo)).skipped == "restricted cwd"


async def test_a_unicode_escaped_or_symlinked_restricted_cwd_is_still_skipped(
    repo, tmp_path: Path, monkeypatch
):
    restricted = tmp_path / "afya"
    restricted.mkdir()
    (tmp_path / "alias").symlink_to(restricted)
    monkeypatch.setattr(importer, "RESTRICTED_ROOT", restricted)
    escaped = str(restricted).replace("afya", "\\u0061fya")
    for cwd_json in (f'"{escaped}"', json.dumps(str(tmp_path / "alias"))):
        path = tmp_path / "r.jsonl"
        path.write_text(
            _codex_lines() + '{"type": "turn_context", "payload": {"cwd": ' + cwd_json + "}}\n"
        )
        assert (await import_transcript(path, repo)).skipped == "restricted cwd", cwd_json


def test_a_tilde_cwd_under_the_restricted_tree_is_restricted(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(importer, "RESTRICTED_ROOT", tmp_path / "dev" / "afya")
    assert importer.is_restricted("~/dev/afya/repo") is True


async def test_an_escaped_cwd_key_into_the_restricted_tree_is_still_skipped(
    repo, tmp_path: Path, monkeypatch
):
    restricted = tmp_path / "afya"
    restricted.mkdir()
    monkeypatch.setattr(importer, "RESTRICTED_ROOT", restricted)
    path = tmp_path / "r.jsonl"
    path.write_text(
        _codex_lines()
        + '{"type": "turn_context", "payload": {"c\\u0077d": ' + json.dumps(str(restricted))
        + "}}\n"
    )
    assert (await import_transcript(path, repo)).skipped == "restricted cwd"


async def test_a_tool_result_appended_later_keeps_the_turn_of_its_call(repo, tmp_path: Path):
    head = [
        {"type": "session_meta", "payload": {"id": "cx-t", "cwd": "/nonexistent/p"}},
        {"type": "turn_context", "timestamp": "2026-10-01T10:00:00Z",
         "payload": {"turn_id": "turn-1"}},
        {"type": "response_item", "timestamp": "2026-10-01T10:00:01Z",
         "payload": {"type": "function_call", "name": "shell", "arguments": "{}",
                     "call_id": "c9"}},
    ]
    path = tmp_path / "r.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in head))
    await import_transcript(path, repo)
    with path.open("a") as f:
        f.write(json.dumps({"type": "response_item", "timestamp": "2026-10-01T10:00:02Z",
                            "payload": {"type": "function_call_output", "call_id": "c9",
                                        "output": "ok"}}) + "\n")
    await import_transcript(path, repo)
    async with (await repo._ensure_pool()).acquire() as con:
        turn = await con.fetchval("SELECT turn_id FROM activity_events WHERE kind='tool_result'")
    assert turn == "turn-1"
