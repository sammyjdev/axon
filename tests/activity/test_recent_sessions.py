"""`recent_sessions` is what `.axon/context.md` is built from: the last sessions of one repo."""

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer

from axon.activity.models import ActivityEvent, ActivitySession
from axon.activity.repository import PostgresActivityRepository

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"  # noqa: S106
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def pg_pool(pg_dsn):
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def repo(pg_dsn, pg_pool):
    async with pg_pool.acquire() as con:
        await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    r = PostgresActivityRepository(pg_dsn)
    await r.ensure_schema()
    yield r
    await r.close()


async def seed_session(repo, sid, *, project="axon", at=T0, harness="claude-code", parent=None):
    await repo.upsert_session(
        ActivitySession(
            session_id=sid, harness=harness, source_id=f"src-{sid}", project=project,
            workspace="/w/axon", parent_session_id=parent, status="ended", coverage="full",
        )
    )


async def seed_event(repo, sid, n, kind, content, at, turn=None, source=None):
    await repo.upsert_event(
        ActivityEvent(
            event_id=f"{sid}-{n}", harness="claude-code", source_id=source or f"{sid}-{n}",
            session_id=sid,
            turn_id=turn,
            occurred_at=at, ingested_at=at, kind=kind, content=content, coverage="full",
            redactions=[],
        )
    )


def say(text, role="user", injected=False):
    c = {"role": role, "text": text}
    if injected:
        c["injected"] = True
    return c


async def test_sessions_come_back_newest_first_and_only_for_the_asked_repo(repo):
    await seed_session(repo, "old")
    await seed_event(repo, "old", 1, "message", say("old work"), T0)
    await seed_session(repo, "new")
    await seed_event(repo, "new", 1, "message", say("new work"), T0 + timedelta(days=1))
    await seed_session(repo, "other", project="pharos")
    await seed_event(repo, "other", 1, "message", say("elsewhere"), T0 + timedelta(days=2))

    got = await repo.recent_sessions("axon", limit=10)

    assert [s.session_id for s in got] == ["new", "old"]
    assert got[0].harness == "claude-code"
    assert got[0].last_at == T0 + timedelta(days=1)


async def test_a_session_imported_late_is_not_newer_than_one_worked_on_later(repo):
    # The importer stamps each session's usage snapshot with the import time.
    await seed_session(repo, "old")
    await seed_event(repo, "old", 1, "message", say("old ask"), T0)
    await seed_event(repo, "old", 2, "usage", {"input_tokens": 1}, T0 + timedelta(days=9))
    await seed_session(repo, "new")
    await seed_event(repo, "new", 1, "message", say("new ask"), T0 + timedelta(days=1))

    found = await repo.recent_sessions("axon", limit=3)

    assert [s.session_id for s in found] == ["new", "old"]


async def test_the_limit_keeps_only_the_newest_sessions(repo):
    for i in range(5):
        await seed_session(repo, f"s{i}")
        await seed_event(repo, f"s{i}", 1, "message", say("x"), T0 + timedelta(hours=i))

    got = await repo.recent_sessions("axon", limit=3)

    assert [s.session_id for s in got] == ["s4", "s3", "s2"]


async def test_first_and_last_prompt_skip_injected_and_assistant_text(repo):
    await seed_session(repo, "s")
    await seed_event(repo, "s", 1, "message", say("<system-reminder>boot", injected=True), T0)
    await seed_event(repo, "s", 2, "message", say("fix the login bug"), T0 + timedelta(minutes=1))
    await seed_event(repo, "s", 3, "message", say("done", role="assistant"), T0 + timedelta(minutes=2))
    await seed_event(repo, "s", 4, "message", say("now add a test"), T0 + timedelta(minutes=3))
    await seed_event(repo, "s", 5, "message", say("auto note", injected=True), T0 + timedelta(minutes=4))

    (s,) = await repo.recent_sessions("axon", limit=3)

    assert s.first_prompt == "fix the login bug"
    assert s.last_prompt == "now add a test"


async def test_a_message_of_only_tabs_and_newlines_is_not_an_ask(repo):
    await seed_session(repo, "s")
    await seed_event(repo, "s", 1, "message", say("\n\t"), T0)
    await seed_event(repo, "s", 2, "message", say("fix login"), T0 + timedelta(minutes=1))
    await seed_event(repo, "s", 3, "message", say("\t\n"), T0 + timedelta(minutes=2))

    (s,) = await repo.recent_sessions("axon", limit=3)

    assert (s.first_prompt, s.last_prompt) == ("fix login", "fix login")


async def test_an_ask_typed_in_several_blocks_keeps_every_block_in_order(repo):
    await seed_session(repo, "s")
    # One typed Claude message, twelve text blocks: one event per block, '::text::<n>'.
    for n in range(12):
        await seed_event(repo, "s", n, "message", say(f"block{n}"), T0, turn="t1",
                         source=f"/t.jsonl::t1::text::{n}")

    (s,) = await repo.recent_sessions("axon", limit=3)

    expected = " ".join(f"block{n}" for n in range(12))
    assert (s.first_prompt, s.last_prompt) == (expected, expected)


async def test_two_codex_messages_in_one_turn_stay_two_asks(repo):
    await seed_session(repo, "s")
    await seed_event(repo, "s", 1, "message", say("fix login"), T0, turn="t1",
                     source="/c.jsonl::s::msg::10")
    await seed_event(repo, "s", 2, "message", say("also fix logout"),
                     T0 + timedelta(minutes=1), turn="t1", source="/c.jsonl::s::msg::20")

    (s,) = await repo.recent_sessions("axon", limit=3)

    assert (s.first_prompt, s.last_prompt) == ("fix login", "also fix logout")


async def test_a_message_of_only_non_breaking_spaces_is_not_an_ask(repo):
    await seed_session(repo, "s")
    await seed_event(repo, "s", 1, "message", say("\u00a0\u00a0"), T0)
    await seed_event(repo, "s", 2, "message", say("fix login"), T0 + timedelta(minutes=1))

    (s,) = await repo.recent_sessions("axon", limit=3)

    assert (s.first_prompt, s.last_prompt) == ("fix login", "fix login")


async def test_an_ask_padded_with_whitespace_is_shown_without_it(repo):
    await seed_session(repo, "s")
    await seed_event(repo, "s", 1, "message", say(" " * 601 + "fix login"), T0)

    (s,) = await repo.recent_sessions("axon", limit=3)

    assert s.first_prompt == "fix login"


async def test_an_ask_after_thousands_of_spaces_is_still_the_ask(repo):
    await seed_session(repo, "s")
    await seed_event(repo, "s", 1, "message", say(" " * 4001 + "fix login"), T0)
    await seed_event(repo, "s", 2, "message", say("later ask"), T0 + timedelta(minutes=1))

    (s,) = await repo.recent_sessions("axon", limit=3)

    assert s.first_prompt == "fix login"


async def test_two_codex_asks_with_one_timestamp_keep_transcript_order(repo):
    await seed_session(repo, "s")
    # event_id order ("s-1" < "s-2") is the reverse of the transcript order.
    await seed_event(repo, "s", 2, "message", say("first typed ask"), T0,
                     source="/c.jsonl::s::msg::600")
    await seed_event(repo, "s", 1, "message", say("last typed ask"), T0,
                     source="/c.jsonl::s::msg::800")

    (s,) = await repo.recent_sessions("axon", limit=3)

    assert (s.first_prompt, s.last_prompt) == ("first typed ask", "last typed ask")


async def test_a_session_with_no_typed_prompt_has_none(repo):
    await seed_session(repo, "s")
    await seed_event(repo, "s", 1, "tool_call", {"type": "tool_use", "name": "Read"}, T0)

    (s,) = await repo.recent_sessions("axon", limit=3)

    assert s.first_prompt is None and s.last_prompt is None


async def test_edit_calls_include_claude_edits_and_codex_patches_but_not_reads(repo):
    await seed_session(repo, "s")
    edit = {"type": "tool_use", "name": "Edit", "input": {"file_path": "/w/axon/a.py"}}
    read = {"type": "tool_use", "name": "Read", "input": {"file_path": "/w/axon/r.py"}}
    patch = {"type": "function_call", "name": "shell",
             "arguments": '{"cmd":"apply_patch <<EOF\\n*** Begin Patch\\n*** Add File: /w/axon/b.py\\n+x\\n*** End Patch\\nEOF"}'}
    await seed_event(repo, "s", 1, "tool_call", edit, T0)
    await seed_event(repo, "s", 2, "tool_call", read, T0 + timedelta(seconds=1))
    await seed_event(repo, "s", 3, "tool_call", patch, T0 + timedelta(seconds=2))

    (s,) = await repo.recent_sessions("axon", limit=3)

    assert s.edit_calls == [edit, patch]


async def test_commits_come_from_the_commit_links_of_the_session(repo, pg_pool):
    await seed_session(repo, "s")
    await seed_event(repo, "s", 1, "message", say("go"), T0)
    async with pg_pool.acquire() as con:
        for target_type, target in [("commit", "a" * 40), ("context_delivery", "zzz"), ("commit", "b" * 40)]:
            await con.execute(
                "INSERT INTO activity_evidence_links (target_type, target_id, session_id, relation) "
                "VALUES ($1, $2, 's', 'record-supported')", target_type, target)

    (s,) = await repo.recent_sessions("axon", limit=3)

    assert s.commits == ["a" * 40, "b" * 40]


async def test_subagent_sessions_and_empty_sessions_are_left_out(repo):
    await seed_session(repo, "main")
    await seed_event(repo, "main", 1, "message", say("hi"), T0)
    await seed_session(repo, "child", parent="main")
    await seed_event(repo, "child", 1, "message", say("sub task"), T0 + timedelta(hours=1))
    await seed_session(repo, "empty")

    got = await repo.recent_sessions("axon", limit=5)

    assert [s.session_id for s in got] == ["main"]


async def test_a_codex_session_stored_by_the_importer_shows_its_typed_prompt(repo):
    from pathlib import Path

    from axon.activity.adapters.codex import parse_codex_session

    parsed = parse_codex_session(Path("tests/fixtures/activity/codex/simple_session.jsonl"))
    sessions = [s.model_copy(update={"project": "axon"}) for s in parsed.sessions]
    offset = int(parsed.cursor.fingerprint.rsplit(":", 1)[1])
    await repo.ingest_batch(sessions, parsed.events, parsed.cursor, byte_offset=offset)

    [recent] = await repo.recent_sessions("axon", limit=3)

    assert (recent.first_prompt, recent.last_prompt) == ("run tests", "run tests")
