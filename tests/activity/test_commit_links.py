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
