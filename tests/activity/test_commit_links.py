import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer

from axon.activity.commit_links import extract_commits, link_commits
from axon.activity.models import ActivityEvent, ActivitySession
from axon.activity.repository import PostgresActivityRepository


def _claude_call(call_id: str, command: str) -> dict:
    return {
        "event_id": f"call-{call_id}",
        "call_id": call_id,
        "kind": "tool_call",
        "content": {
            "type": "tool_use",
            "id": call_id,
            "name": "Bash",
            "input": {"command": command},
        },
    }


def _claude_result(call_id: str, text) -> dict:
    return {
        "event_id": f"res-{call_id}",
        "call_id": call_id,
        "kind": "tool_result",
        "content": {"type": "tool_result", "tool_use_id": call_id, "content": text},
    }


def _commits(command: str, output, workspace: str | None = "/ws"):
    events = [_claude_call("c1", command), _claude_result("c1", output)]
    return [(c.short_hash, c.event_id) for c in extract_commits(events, workspace)]


def test_a_commit_result_yields_the_short_hash_and_the_result_event() -> None:
    out = "[main 3197c6d] fix: x\n 1 file changed, 1 insertion(+)\n"
    assert _commits("git commit -m 'fix: x'", out) == [("3197c6d", "res-c1")]


def test_root_commit_output_is_recognised() -> None:
    out = "[main (root-commit) a1b2c3d] init\n 1 file changed\n"
    assert _commits("git commit -m init", out) == [("a1b2c3d", "res-c1")]


def test_detached_head_output_is_recognised() -> None:
    out = "[detached HEAD 0badf00] wip\n"
    assert _commits("git commit -m wip", out) == [("0badf00", "res-c1")]


def test_amend_output_is_recognised() -> None:
    out = "[feat/x 9f8e7d6] better\n Date: Mon Oct 12 10:00:00 2026\n 1 file changed\n"
    assert _commits("git commit --amend -m better", out) == [("9f8e7d6", "res-c1")]


def test_commit_inside_an_and_chain_is_recognised() -> None:
    out = "[main 1234567] chore\n"
    assert _commits("git add -A && git commit -m chore && git push", out) == [("1234567", "res-c1")]


def test_text_block_list_results_are_read() -> None:
    out = [{"type": "text", "text": "[main 7654321] chore\n"}]
    assert _commits("git -C /repo commit -m chore", out) == [("7654321", "res-c1")]


def test_failed_commits_link_nothing() -> None:
    assert (
        _commits("git commit -m x", "On branch main\nnothing to commit, working tree clean") == []
    )
    assert _commits("git commit -m x", "ruff....Failed\nhook id: ruff\nexit code: 1\n") == []


def test_bracket_lines_from_commands_that_are_not_commits_link_nothing() -> None:
    assert _commits("git log --oneline", "[main 3197c6d] looks like a commit line\n") == []


def test_commit_directory_comes_from_dash_c_then_cd_then_workspace() -> None:
    out = "[main 3197c6d] x\n"

    def d(cmd, ws="/ws"):
        return extract_commits([_claude_call("c1", cmd), _claude_result("c1", out)], ws)[
            0
        ].directory

    assert d("git -C /other/repo commit -m x") == "/other/repo"
    assert d("git -C sub commit -m x") == "/ws/sub"
    assert d("cd /elsewhere && git commit -m x") == "/elsewhere"
    assert d("git commit -m x") == "/ws"


def test_codex_function_call_and_output_are_matched() -> None:
    events = [
        {
            "event_id": "e1",
            "call_id": "k1",
            "kind": "tool_call",
            "content": {
                "type": "function_call",
                "name": "shell",
                "call_id": "k1",
                "arguments": json.dumps(
                    {"command": ["bash", "-lc", "git commit -m 'x'"], "workdir": "/cx"}
                ),
            },
        },
        {
            "event_id": "e2",
            "call_id": "k1",
            "kind": "tool_result",
            "content": {
                "type": "function_call_output",
                "call_id": "k1",
                "output": json.dumps(
                    {"output": "[main abcdef0] x\n 1 file changed\n", "metadata": {}}
                ),
            },
        },
    ]
    found = extract_commits(events, "/ws")
    assert [(c.short_hash, c.event_id, c.directory) for c in found] == [("abcdef0", "e2", "/cx")]


# ---- database-backed behaviour ------------------------------------------------


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16",
        username="axon",
        password="axon",
        dbname="axon",  # noqa: S106
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def repo(pg_dsn):
    con = await asyncpg.connect(pg_dsn)
    await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await con.close()
    repo = PostgresActivityRepository(pg_dsn)
    await repo.ensure_schema()
    yield repo
    await repo.close()


def _git(cwd: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("a")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "first change")
    return tmp_path


async def _seed(repo, session_id: str, workspace: str, short: str, ok: bool = True) -> None:
    await repo.upsert_session(
        ActivitySession(
            session_id=session_id,
            harness="claude-code",
            source_id=f"s-{session_id}",
            project="p",
            workspace=workspace,
            status="complete",
            coverage="full",
        )
    )
    now = datetime(2026, 10, 1, tzinfo=UTC)
    out = f"[main {short}] first change\n 1 file changed\n" if ok else "nothing to commit"
    for i, (kind, content) in enumerate(
        [
            (
                "tool_call",
                {
                    "type": "tool_use",
                    "id": "c1",
                    "name": "Bash",
                    "input": {"command": "git commit -m x"},
                },
            ),
            ("tool_result", {"type": "tool_result", "tool_use_id": "c1", "content": out}),
        ]
    ):
        await repo.upsert_event(
            ActivityEvent(
                event_id=f"{session_id}-{i}",
                harness="claude-code",
                source_id=f"{session_id}-src-{i}",
                session_id=session_id,
                call_id="c1",
                occurred_at=now.replace(second=i),
                ingested_at=now,
                kind=kind,
                content=content,
                coverage="full",
                redactions=[],
            )
        )


async def test_commit_link_stores_the_full_hash_resolved_in_the_session_workspace(
    repo, git_repo
) -> None:
    full = _git(git_repo, "rev-parse", "HEAD")
    await _seed(repo, "s1", str(git_repo), full[:7])

    added = await link_commits(repo, "s1")

    links = await repo.get_evidence_links(target_type="commit", target_id=full)
    assert added == 1
    assert [(link["session_id"], link["event_id"], link["relation"]) for link in links] == [
        ("s1", "s1-1", "produced")
    ]


async def test_linking_a_session_twice_adds_no_second_link(repo, git_repo) -> None:
    full = _git(git_repo, "rev-parse", "HEAD")
    await _seed(repo, "s1", str(git_repo), full[:7])
    await link_commits(repo, "s1")

    assert await link_commits(repo, "s1") == 0
    assert len(await repo.get_evidence_links(target_type="commit", target_id=full)) == 1


async def test_commit_link_keeps_the_short_hash_when_the_directory_is_gone(repo, tmp_path) -> None:
    await _seed(repo, "s1", str(tmp_path / "deleted"), "abc1234")

    await link_commits(repo, "s1")

    assert len(await repo.get_evidence_links(target_type="commit", target_id="abc1234")) == 1


async def test_a_failed_commit_in_the_session_adds_no_link(repo, git_repo) -> None:
    await _seed(repo, "s1", str(git_repo), "abc1234", ok=False)

    assert await link_commits(repo, "s1") == 0


async def test_evidence_link_accepts_the_produced_relation_and_rejects_unknown_ones(repo) -> None:
    await repo.record_evidence_link(
        target_type="commit",
        target_id="abc1234",
        session_id="s",
        turn_id=None,
        event_id=None,
        relation="produced",
    )
    with pytest.raises(ValueError):
        await repo.record_evidence_link(
            target_type="commit",
            target_id="abc1234",
            session_id="s",
            turn_id=None,
            event_id=None,
            relation="bogus",
        )


# ---- quiet commits and Codex exec scripts (what real transcripts on this machine hold) ----


async def _seed_call(
    repo, workspace: str, command: str, output, when: datetime, *, is_error: bool = False
) -> None:
    await repo.upsert_session(
        ActivitySession(
            session_id="q1", harness="claude-code", source_id="s-q1",
            project="p", workspace=workspace, status="complete", coverage="full",
        )
    )
    for i, (kind, content) in enumerate(
        [
            ("tool_call", {"type": "tool_use", "id": "c9", "name": "Bash", "input": {"command": command}}),
            ("tool_result", {"type": "tool_result", "tool_use_id": "c9", "content": output,
                             "is_error": is_error}),
        ]
    ):
        await repo.upsert_event(
            ActivityEvent(
                event_id=f"q1-{i}", harness="claude-code", source_id=f"q1-src-{i}",
                session_id="q1", call_id="c9", occurred_at=when, ingested_at=when,
                kind=kind, content=content, coverage="full", redactions=[],
            )
        )


async def _links(repo) -> list[tuple[str, str]]:
    pool = await repo._ensure_pool()
    rows = await pool.fetch("SELECT target_id, event_id FROM activity_evidence_links")
    return [(r["target_id"], r["event_id"]) for r in rows]


async def test_a_quiet_commit_is_found_by_its_subject_in_the_workspace_log(repo, git_repo) -> None:
    # Given a session that ran `git commit -qm "first change"`, which prints nothing
    full = _git(git_repo, "rev-parse", "HEAD")
    await _seed_call(repo, str(git_repo), 'git add -A && git commit -qm "first change"', "", datetime.now(UTC))

    # When the session's commits are linked
    added = await link_commits(repo, "q1")

    # Then the commit with that subject, made just before the result, is linked
    assert added == 1
    assert await _links(repo) == [(full, "q1-1")]


async def test_a_heredoc_commit_message_is_matched_by_its_first_line(repo, git_repo) -> None:
    full = _git(git_repo, "rev-parse", "HEAD")
    command = (
        "git commit -q -m \"$(cat <<'EOF'\nfirst change\n\nbody line\nEOF\n)\""
    )
    await _seed_call(repo, str(git_repo), command, "", datetime.now(UTC))

    assert await link_commits(repo, "q1") == 1
    assert await _links(repo) == [(full, "q1-1")]


async def test_a_quiet_commit_matches_no_commit_made_long_before_the_call(repo, git_repo) -> None:
    # Given the same subject, but the call happened two days after that commit
    later = datetime.now(UTC) + timedelta(days=2)
    await _seed_call(repo, str(git_repo), 'git commit -qm "first change"', "", later)

    assert await link_commits(repo, "q1") == 0


async def test_a_quiet_commit_that_had_nothing_to_commit_links_nothing(repo, git_repo) -> None:
    await _seed_call(
        repo, str(git_repo), 'git commit -qm "first change"',
        "On branch main\nnothing to commit, working tree clean", datetime.now(UTC),
    )

    assert await link_commits(repo, "q1") == 0


def test_codex_exec_script_commits_are_read_from_the_nested_output() -> None:
    # Given Codex's `exec` custom tool, whose script calls tools.exec_command
    script = 'text(await tools.exec_command({cmd:"git commit -m \\"x\\"",workdir:"/cx"}));\n'
    output = [
        {"type": "input_text", "text": "Script completed\nOutput:\n"},
        {"type": "input_text", "text": json.dumps({"exit_code": 0, "output": "[main abcdef0] x\n 1 file changed\n"})},
    ]
    events = [
        {"event_id": "e1", "call_id": "k1", "kind": "tool_call",
         "content": {"type": "custom_tool_call", "name": "exec", "call_id": "k1", "input": script}},
        {"event_id": "e2", "call_id": "k1", "kind": "tool_result",
         "content": {"type": "custom_tool_call_output", "call_id": "k1", "output": output}},
    ]

    found = extract_commits(events, "/ws")

    assert [(c.short_hash, c.event_id, c.directory) for c in found] == [("abcdef0", "e2", "/cx")]


async def test_a_message_read_from_a_stdin_heredoc_is_matched_by_its_first_line(repo, git_repo) -> None:
    full = _git(git_repo, "rev-parse", "HEAD")
    command = "git add -A && git commit -q -F - <<'EOF'\nfirst change\n\nbody line\nEOF"
    await _seed_call(repo, str(git_repo), command, "", datetime.now(UTC))

    assert await link_commits(repo, "q1") == 1
    assert await _links(repo) == [(full, "q1-1")]


# ---- review round 1 ----


async def test_a_rejected_quiet_commit_does_not_claim_an_earlier_commit_with_the_same_subject(
    repo, git_repo
) -> None:
    # Given a pre-commit hook rejected `git commit -qm "first change"` and the tool result is an error
    await _seed_call(
        repo, str(git_repo), 'git commit -qm "first change"', "ruff....Failed",
        datetime.now(UTC), is_error=True,
    )

    assert await link_commits(repo, "q1") == 0


def test_the_directory_is_the_one_the_commit_itself_runs_in() -> None:
    out = "[main abc1234] x\n"

    def d(cmd):
        return extract_commits([_claude_call("c1", cmd), _claude_result("c1", out)], "/ws")[0].directory

    assert d("git -C /first status && git -C /second commit -m x") == "/second"
    assert d("cd /elsewhere && git -C sub commit -m x") == "/elsewhere/sub"
    assert d("cd /a && git status; cd /b && git commit -m x") == "/b"


def test_a_codex_exec_script_with_several_commands_finds_the_commit_among_them() -> None:
    script = (
        'text(await tools.exec_command({cmd:"git status --short"}));\n'
        'text(await tools.exec_command({cmd:"git commit -m \\"x\\""}));\n'
    )
    events = [
        {"event_id": "e1", "call_id": "k1", "kind": "tool_call",
         "content": {"type": "custom_tool_call", "name": "exec", "call_id": "k1", "input": script}},
        {"event_id": "e2", "call_id": "k1", "kind": "tool_result",
         "content": {"type": "custom_tool_call_output", "call_id": "k1",
                     "output": [{"type": "input_text", "text": "[main abcdef0] x\n"}]}},
    ]

    assert [c.short_hash for c in extract_commits(events, "/ws")] == ["abcdef0"]


async def test_a_short_link_is_not_duplicated_once_the_full_hash_resolves(repo, git_repo) -> None:
    full = _git(git_repo, "rev-parse", "HEAD")
    await _seed(repo, "s1", str(git_repo), full[:7])
    await repo.add_commit_link(commit=full[:7], session_id="s1", event_id="s1-1")

    assert await link_commits(repo, "s1") == 0
    assert len(await _links(repo)) == 1


# ---- review round 2 ----


def _codex_exec(calls: list[tuple[str, str | None]], output_text: str) -> list[dict]:
    script = "".join(
        "text(await tools.exec_command({cmd:%s%s}));\n"
        % (json.dumps(cmd), f",workdir:{json.dumps(wd)}" if wd else "")
        for cmd, wd in calls
    )
    return [
        {"event_id": "e1", "call_id": "k1", "kind": "tool_call",
         "content": {"type": "custom_tool_call", "name": "exec", "call_id": "k1", "input": script}},
        {"event_id": "e2", "call_id": "k1", "kind": "tool_result",
         "content": {"type": "custom_tool_call_output", "call_id": "k1",
                     "output": [{"type": "input_text", "text": output_text}]}},
    ]


def test_a_codex_commit_that_exited_non_zero_is_not_a_quiet_commit() -> None:
    events = _codex_exec(
        [('git commit -qm "first change"', "/cx")],
        json.dumps({"exit_code": 1, "output": "ruff....Failed\n"}),
    )

    assert extract_commits(events, "/ws") == []


def test_each_commit_in_a_chain_gets_its_own_directory() -> None:
    out = "[main aaaaaaa] one\n[main bbbbbbb] two\n"
    events = [_claude_call("c1", "git -C /a commit -m one && git -C /b commit -m two"),
              _claude_result("c1", out)]

    found = extract_commits(events, "/ws")

    assert [(c.short_hash, c.directory) for c in found] == [("aaaaaaa", "/a"), ("bbbbbbb", "/b")]


def test_each_codex_exec_command_keeps_its_own_workdir() -> None:
    events = _codex_exec(
        [('git commit -m "one"', "/a"), ('git commit -m "two"', "/b")],
        "[main aaaaaaa] one\n[main bbbbbbb] two\n",
    )

    assert [c.directory for c in extract_commits(events, "/ws")] == ["/a", "/b"]


def test_every_quiet_commit_in_a_chain_is_looked_up_by_its_own_subject() -> None:
    events = [_claude_call("c1", 'git commit -qm "one" && git commit -qm "two"'),
              _claude_result("c1", "")]

    assert [c.subject for c in extract_commits(events, "/ws")] == ["one", "two"]


def test_a_printed_commit_and_a_quiet_one_in_the_same_chain_are_both_found() -> None:
    events = [_claude_call("c1", 'git commit -m "one" && git commit -qm "two"'),
              _claude_result("c1", "[main aaaaaaa] one\n")]

    found = extract_commits(events, "/ws")

    assert [(c.short_hash, c.subject) for c in found] == [("aaaaaaa", None), (None, "two")]


def test_an_unquoted_one_word_message_is_a_subject() -> None:
    events = [_claude_call("c1", "git commit -qm init"), _claude_result("c1", "")]

    assert [c.subject for c in extract_commits(events, "/ws")] == ["init"]


async def test_a_commit_made_after_the_result_is_not_claimed(repo, git_repo) -> None:
    # Given the result was recorded 30 seconds before the commit with that subject was made
    made = datetime.fromisoformat(_git(git_repo, "log", "-1", "--format=%cI"))
    await _seed_call(repo, str(git_repo), 'git commit -qm "first change"', "", made - timedelta(seconds=30))

    assert await link_commits(repo, "q1") == 0


# ---- review round 3 ----


def test_a_git_log_that_prints_bracket_lines_is_not_a_commit() -> None:
    assert _commits("git log --format='[main %h] %s' --grep=commit", "[main abc1234] x\n") == []


def test_a_dry_run_commit_is_not_a_commit() -> None:
    events = [_claude_call("c1", "git commit --dry-run -qm one"), _claude_result("c1", "")]

    assert extract_commits(events, "/ws") == []


def test_a_printed_line_is_paired_with_the_commit_of_the_same_subject() -> None:
    # Given the first commit had nothing to commit and only the second printed its line
    events = [_claude_call("c1", "git -C /a commit -m one; git -C /b commit -m two"),
              _claude_result("c1", "nothing to commit\n[main bbbbbbb] two\n")]

    found = extract_commits(events, "/ws")

    assert [(c.short_hash, c.directory) for c in found] == [("bbbbbbb", "/b")]


def test_a_codex_process_that_exited_non_zero_is_not_a_quiet_commit() -> None:
    events = _codex_exec(
        [('git commit -qm "first change"', None)], "Process exited with code 1\nOutput:\nruff....Failed"
    )

    assert extract_commits(events, "/ws") == []


def test_a_codex_exec_call_without_workdir_does_not_inherit_the_previous_one() -> None:
    events = _codex_exec([("git status", "/other"), ('git commit -m "x"', None)], "[main abcdef0] x\n")

    assert [c.directory for c in extract_commits(events, "/workspace")] == ["/workspace"]


def test_a_dash_q_inside_the_message_does_not_make_the_commit_quiet() -> None:
    events = [_claude_call("c1", "git -C /a commit -m 'run -q option' && git -C /b commit -m two"),
              _claude_result("c1", "[main aaaaaaa] run -q option\n[main bbbbbbb] two\n")]

    found = extract_commits(events, "/ws")

    assert [(c.short_hash, c.directory) for c in found] == [("aaaaaaa", "/a"), ("bbbbbbb", "/b")]


def test_the_word_commit_inside_a_dash_c_path_keeps_the_whole_path() -> None:
    events = [_claude_call("c1", "git -C /repo/commit/project commit -m one"),
              _claude_result("c1", "[main aaaaaaa] one\n")]

    assert [c.directory for c in extract_commits(events, "/ws")] == ["/repo/commit/project"]


# ---- review round 4 ----


def test_a_dry_run_flag_after_the_message_is_still_a_dry_run() -> None:
    events = [_claude_call("c1", 'git commit -qm "one" --dry-run'), _claude_result("c1", "")]

    assert extract_commits(events, "/ws") == []


def test_git_commit_inside_the_quoted_message_is_part_of_the_subject() -> None:
    events = [_claude_call("c1", 'git commit -qm "fix git commit detection"'), _claude_result("c1", "")]

    assert [c.subject for c in extract_commits(events, "/ws")] == ["fix git commit detection"]


def test_a_codex_exec_call_with_quoted_keys_is_read() -> None:
    script = 'text(await tools.exec_command({"cmd":"git commit -m x","workdir":"/cx"}));\n'
    events = [
        {"event_id": "e1", "call_id": "k1", "kind": "tool_call",
         "content": {"type": "custom_tool_call", "name": "exec", "call_id": "k1", "input": script}},
        {"event_id": "e2", "call_id": "k1", "kind": "tool_result",
         "content": {"type": "custom_tool_call_output", "call_id": "k1",
                     "output": [{"type": "input_text", "text": "[main abc1234] x\n"}]}},
    ]

    assert [(c.short_hash, c.directory) for c in extract_commits(events, "/ws")] == [("abc1234", "/cx")]


def test_a_quoted_dash_c_directory_is_kept() -> None:
    events = [_claude_call("c1", 'git -C "/my repo" commit -m one'), _claude_result("c1", "[main aaaaaaa] one\n")]

    assert [c.directory for c in extract_commits(events, "/ws")] == ["/my repo"]


async def test_a_quiet_commit_recorded_in_the_same_second_as_its_result_is_found(repo, git_repo) -> None:
    full = _git(git_repo, "rev-parse", "HEAD")
    made = datetime.fromisoformat(_git(git_repo, "log", "-1", "--format=%cI"))
    await _seed_call(repo, str(git_repo), 'git commit -qm "first change"', "", made + timedelta(milliseconds=100))

    assert await link_commits(repo, "q1") == 1
    assert await _links(repo) == [(full, "q1-1")]


# ---- review round 5: commands are tokenized, not pattern-matched ----


def _cmds(command: str, workspace: str = "/ws"):
    events = [_claude_call("c1", command), _claude_result("c1", "")]
    return [(c.subject, c.directory) for c in extract_commits(events, workspace)]


def test_a_commit_written_into_a_heredoc_file_is_not_run() -> None:
    assert _cmds("cat > commit.sh <<'EOF'\ngit commit -qm \"fix\"\nEOF") == []


def test_a_stdin_heredoc_subject_may_contain_git_commit() -> None:
    assert _cmds("git commit -q -F - <<'EOF'\nfix git commit detection\n\nbody\nEOF") == [
        ("fix git commit detection", "/ws")
    ]


def test_a_dash_q_after_the_message_still_makes_the_commit_quiet() -> None:
    events = [_claude_call("c1", "git -C /a commit -m one -q && git -C /b commit -m two"),
              _claude_result("c1", "[main bbbbbbb] two\n")]

    found = extract_commits(events, "/ws")

    assert [(c.short_hash, c.subject, c.directory) for c in found] == [
        (None, "one", "/a"), ("bbbbbbb", None, "/b")
    ]


def test_a_dry_run_of_another_command_does_not_drop_the_commit() -> None:
    assert _cmds('git commit -qm "fix: x" && git push --dry-run') == [("fix: x", "/ws")]


def test_a_codex_exec_call_with_single_quoted_strings_is_read() -> None:
    script = "text(await tools.exec_command({cmd:'git commit -m x',workdir:'/repo'}));\n"
    events = [
        {"event_id": "e1", "call_id": "k1", "kind": "tool_call",
         "content": {"type": "custom_tool_call", "name": "exec", "call_id": "k1", "input": script}},
        {"event_id": "e2", "call_id": "k1", "kind": "tool_result",
         "content": {"type": "custom_tool_call_output", "call_id": "k1",
                     "output": [{"type": "input_text", "text": "[main abcdef0] x\n"}]}},
    ]

    assert [(c.short_hash, c.directory) for c in extract_commits(events, "/ws")] == [("abcdef0", "/repo")]


def test_a_home_relative_directory_is_expanded() -> None:
    assert _cmds('git -C ~/projects/axon commit -qm "x"', "/workspace") == [
        ("x", str(Path.home() / "projects/axon"))
    ]


def test_every_dash_c_applies_in_turn() -> None:
    assert _cmds('git -C /projects -C axon commit -qm "x"') == [("x", "/projects/axon")]
