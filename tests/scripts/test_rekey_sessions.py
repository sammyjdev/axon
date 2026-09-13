# tests/scripts/test_rekey_sessions.py
"""Tests for scripts/rekey_sessions.py.

Verifies:
- Dry run by default lists matching rows in sessions and session_memory, changing nothing.
- Refuses --apply without explicit scope flag (exit 2).
- Rejects mutually exclusive scope flags (exit 2).
- With --apply and --all, re-keys both tables so no rows hold absolute paths.
- With --apply and --sessions, re-keys sessions and leaves session_memory untouched.
- With --apply and --session-memory, re-keys session_memory and leaves sessions untouched.
- Preserves clean rows without leading slash byte-identical before and after.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from axon.store.pg_migrations import apply_pg_migrations
from scripts.rekey_sessions import (
    apply_rekey_session_memory,
    apply_rekey_sessions,
    inspect_session_memory,
    inspect_sessions,
    main,
    resolve_key,
    run,
)


def _get_test_pg_url() -> str:
    env = os.environ.get("AXON_PG_URL")
    if env:
        return env
    from axon.config.runtime import load_runtime_config

    return load_runtime_config().pg_url


async def _seed_data() -> None:
    pg_url = _get_test_pg_url()
    con = await asyncpg.connect(pg_url)
    try:
        await apply_pg_migrations(con)
        await con.execute("TRUNCATE sessions, session_memory CASCADE")
        await con.execute(
            """
            INSERT INTO sessions (id, agent, repo, started_at, context_payload) VALUES
            ('sess-1', 'codex', '/Users/samdev/dev/axon', '2026-09-13T00:00:00Z', '{}'),
            ('sess-2', 'luna-2', '/Users/samdev/dev/other-project', '2026-09-13T01:00:00Z', '{}'),
            ('sess-3', 'manual', 'clean-repo', '2026-09-13T02:00:00Z', '{"notes": 1}'),
            ('sess-4', 'claude-code', 'another-clean', '2026-09-13T03:00:00Z', '{"ok": true}')
            """
        )
        await con.execute(
            """
            INSERT INTO session_memory (id, project, summary, raw_turns, created_at) VALUES
            (10, '/Users/samdev/dev/axon', 'prior session memory', 5, '2026-09-13T00:00:00Z'),
            (11, 'clean-proj', 'clean session memory', 3, '2026-09-13T01:00:00Z')
            """
        )
    finally:
        await con.close()


def test_resolve_key_returns_basename_for_nonexistent_paths() -> None:
    assert resolve_key("/Users/samdev/dev/axon") == "axon"
    assert resolve_key("/var/log/my_app") == "my_app"


async def test_dry_run_lists_and_changes_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _seed_data()
    pg_url = _get_test_pg_url()

    exit_code = await run([])
    assert exit_code == 0

    out, err = capsys.readouterr()
    assert "would re-key session sess-1: /Users/samdev/dev/axon -> axon" in out
    assert "would re-key session sess-2: /Users/samdev/dev/other-project -> other-project" in out
    assert "would re-key session_memory 10: /Users/samdev/dev/axon -> axon" in out
    assert "would re-key 2 session(s) and 1 session_memory row(s)" in out
    assert "--apply" in out

    # Assert rows in Postgres are untouched
    con = await asyncpg.connect(pg_url)
    try:
        s1 = await con.fetchrow("SELECT repo FROM sessions WHERE id = 'sess-1'")
        s2 = await con.fetchrow("SELECT repo FROM sessions WHERE id = 'sess-2'")
        m10 = await con.fetchrow("SELECT project FROM session_memory WHERE id = 10")
        assert s1 is not None and s1["repo"] == "/Users/samdev/dev/axon"
        assert s2 is not None and s2["repo"] == "/Users/samdev/dev/other-project"
        assert m10 is not None and m10["project"] == "/Users/samdev/dev/axon"
    finally:
        await con.close()


async def test_apply_without_scope_flag_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _seed_data()
    exit_code = await run(["--apply"])
    assert exit_code == 2

    out, err = capsys.readouterr()
    assert "Refusing to re-key: pass --sessions, --session-memory, or --all with --apply." in err


async def test_mutually_exclusive_scope_flags_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = await run(["--all", "--sessions"])
    assert exit_code == 2
    out, err = capsys.readouterr()
    assert "--all and --sessions/--session-memory are mutually exclusive" in err

    exit_code2 = await run(["--all", "--session-memory"])
    assert exit_code2 == 2


async def test_apply_all_rekeys_both_tables_and_preserves_clean_rows(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _seed_data()
    pg_url = _get_test_pg_url()

    con = await asyncpg.connect(pg_url)
    try:
        s3_before = dict(await con.fetchrow("SELECT * FROM sessions WHERE id = 'sess-3'"))
        s4_before = dict(await con.fetchrow("SELECT * FROM sessions WHERE id = 'sess-4'"))
        m11_before = dict(await con.fetchrow("SELECT * FROM session_memory WHERE id = 11"))
    finally:
        await con.close()

    exit_code = await run(["--apply", "--all"])
    assert exit_code == 0

    out, err = capsys.readouterr()
    assert "re-keyed session sess-1: /Users/samdev/dev/axon -> axon" in out
    assert "re-keyed session sess-2: /Users/samdev/dev/other-project -> other-project" in out
    assert "re-keyed session_memory 10: /Users/samdev/dev/axon -> axon" in out
    assert "re-keyed 2 session(s) and 1 session_memory row(s)" in out

    con = await asyncpg.connect(pg_url)
    try:
        slashed_sessions = await con.fetch("SELECT id FROM sessions WHERE repo LIKE '/%'")
        assert len(slashed_sessions) == 0

        slashed_memories = await con.fetch("SELECT id FROM session_memory WHERE project LIKE '/%'")
        assert len(slashed_memories) == 0

        s1 = await con.fetchrow("SELECT repo FROM sessions WHERE id = 'sess-1'")
        s2 = await con.fetchrow("SELECT repo FROM sessions WHERE id = 'sess-2'")
        m10 = await con.fetchrow("SELECT project FROM session_memory WHERE id = 10")
        assert s1 is not None and s1["repo"] == "axon"
        assert s2 is not None and s2["repo"] == "other-project"
        assert m10 is not None and m10["project"] == "axon"

        # Assert clean rows are byte-identical before and after
        s3_after = dict(await con.fetchrow("SELECT * FROM sessions WHERE id = 'sess-3'"))
        s4_after = dict(await con.fetchrow("SELECT * FROM sessions WHERE id = 'sess-4'"))
        m11_after = dict(await con.fetchrow("SELECT * FROM session_memory WHERE id = 11"))
        assert s3_after == s3_before
        assert s4_after == s4_before
        assert m11_after == m11_before
    finally:
        await con.close()


async def test_apply_sessions_scope_leaves_session_memory_untouched(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _seed_data()
    pg_url = _get_test_pg_url()

    exit_code = await run(["--apply", "--sessions"])
    assert exit_code == 0

    out, err = capsys.readouterr()
    assert "re-keyed session sess-1" in out
    assert "re-keyed 2 session(s)" in out
    assert "session_memory" not in out

    con = await asyncpg.connect(pg_url)
    try:
        # Sessions re-keyed
        s1 = await con.fetchrow("SELECT repo FROM sessions WHERE id = 'sess-1'")
        assert s1 is not None and s1["repo"] == "axon"

        # session_memory untouched
        m10 = await con.fetchrow("SELECT project FROM session_memory WHERE id = 10")
        assert m10 is not None and m10["project"] == "/Users/samdev/dev/axon"
    finally:
        await con.close()


async def test_apply_session_memory_scope_leaves_sessions_untouched(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _seed_data()
    pg_url = _get_test_pg_url()

    exit_code = await run(["--apply", "--session-memory"])
    assert exit_code == 0

    out, err = capsys.readouterr()
    assert "re-keyed session_memory 10" in out
    assert "re-keyed 1 session_memory row(s)" in out
    assert "re-keyed session sess-1" not in out

    con = await asyncpg.connect(pg_url)
    try:
        # session_memory re-keyed
        m10 = await con.fetchrow("SELECT project FROM session_memory WHERE id = 10")
        assert m10 is not None and m10["project"] == "axon"

        # Sessions untouched
        s1 = await con.fetchrow("SELECT repo FROM sessions WHERE id = 'sess-1'")
        assert s1 is not None and s1["repo"] == "/Users/samdev/dev/axon"
    finally:
        await con.close()


async def test_no_matching_rows_reports_cleanly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pg_url = _get_test_pg_url()
    con = await asyncpg.connect(pg_url)
    try:
        await apply_pg_migrations(con)
        await con.execute("TRUNCATE sessions, session_memory CASCADE")
    finally:
        await con.close()

    exit_code = await run([])
    assert exit_code == 0

    out, err = capsys.readouterr()
    assert "(no matching session rows found)" in out
    assert "(no matching session_memory rows found)" in out
    assert "would re-key 0 session(s) and 0 session_memory row(s)" in out


def test_main_runs_without_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Dry run with --help or empty args through main()
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


async def test_credentials_redacted_on_connection_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "secret_db_password_xyz987"  # noqa: S105
    bad_pg_url = f"postgresql://axon_admin:{secret}@127.0.0.1:1/axon_test"

    with pytest.raises(ConnectionError) as exc_info:
        await inspect_sessions(bad_pg_url)
    msg = str(exc_info.value)
    assert secret not in msg
    assert "127.0.0.1" in msg

    with pytest.raises(ConnectionError) as exc_info_apply:
        await apply_rekey_sessions(bad_pg_url)
    msg_apply = str(exc_info_apply.value)
    assert secret not in msg_apply
    assert "127.0.0.1" in msg_apply

    with pytest.raises(ConnectionError) as exc_info_mem:
        await inspect_session_memory(bad_pg_url)
    msg_mem = str(exc_info_mem.value)
    assert secret not in msg_mem
    assert "127.0.0.1" in msg_mem

    with pytest.raises(ConnectionError) as exc_info_mem_apply:
        await apply_rekey_session_memory(bad_pg_url)
    msg_mem_apply = str(exc_info_mem_apply.value)
    assert secret not in msg_mem_apply
    assert "127.0.0.1" in msg_mem_apply

    exit_code = await run(["--pg-url", bad_pg_url, "--sessions"])
    assert exit_code == 1
    _, err = capsys.readouterr()
    assert secret not in err
    assert "127.0.0.1" in err

