"""`.axon/context.md` opens every session in a repo with where the last sessions left off.

Sessions are seeded straight into the 0006 activity tables in the shape the B/D contract
fixes (message role/text/injected, tool_call payloads, commit evidence links).
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from axon.activity.models import ActivityEvent, ActivitySession
from axon.activity.repository import PostgresActivityRepository
from axon.core.decision import Decision
from axon.hooks import file_bridge
from axon.hooks.file_bridge import refresh_context_for_cwd, update_context_file
from axon.store.lessons import LessonHandle

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


class _Store:
    def __init__(self, decisions=()):
        self.decisions = list(decisions)

    async def find_decisions_by_repo(self, repo, limit=None):
        return self.decisions[:limit]


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()  # noqa: S603, S607


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "axon"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
         "-m", "feat: render recent sessions")
    return root


@pytest.fixture
async def activity():
    dsn = os.environ["AXON_PG_URL"]
    repo = PostgresActivityRepository(dsn)
    try:
        await repo.ensure_schema()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"no test Postgres: {exc}")
    pool = await repo._ensure_pool()
    async with pool.acquire() as con:
        await con.execute(
            "TRUNCATE activity_evidence_links, activity_events, activity_sessions CASCADE"
        )
    yield repo
    await repo.close()


async def _session(repo, sid, *, workspace="/w/axon", harness="claude-code", project="axon"):
    await repo.upsert_session(ActivitySession(
        session_id=sid, harness=harness, source_id=f"src-{sid}", project=project,
        workspace=workspace, status="ended", coverage="full",
    ))


async def _event(repo, sid, n, kind, content, at):
    await repo.upsert_event(ActivityEvent(
        event_id=f"{sid}-{n}", harness="claude-code", source_id=f"{sid}-{n}", session_id=sid,
        occurred_at=at, ingested_at=at, kind=kind, content=content, coverage="full",
        redactions=[],
    ))


async def _commit_link(repo, sid, sha):
    pool = await repo._ensure_pool()
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO activity_evidence_links (target_type, target_id, session_id, relation)"
            " VALUES ('commit', $1, $2, 'record-supported')", sha, sid)


def _say(text, role="user", injected=False):
    return {"role": role, "text": text, **({"injected": True} if injected else {})}


def _edit(path, name="Edit"):
    return {"type": "tool_use", "name": name, "input": {"file_path": path}}


async def test_the_last_sessions_show_asks_files_and_commits_newest_first(activity, git_repo):
    root = git_repo
    sha = _git(root, "rev-parse", "HEAD")
    await _session(activity, "older-session", workspace=str(root))
    await _event(activity, "older-session", 1, "message", _say("old ask"), T0)
    await _session(activity, "newer-session", workspace=str(root), harness="codex")
    await _event(activity, "newer-session", 1, "message", _say("boot", injected=True), T0 + timedelta(days=1))
    await _event(activity, "newer-session", 2, "message", _say("fix the  login\nbug"), T0 + timedelta(days=1, minutes=1))
    await _event(activity, "newer-session", 3, "tool_call", _edit(f"{root}/src/a.py"), T0 + timedelta(days=1, minutes=2))
    await _event(activity, "newer-session", 4, "tool_call", _edit(f"{root}/src/a.py", "Write"), T0 + timedelta(days=1, minutes=3))
    patch = {"type": "custom_tool_call", "name": "exec",
             "input": f'text(await tools.apply_patch("*** Begin Patch\\n*** Update File: {root}/docs/b.md\\n@@\\n*** End Patch"))'}
    await _event(activity, "newer-session", 5, "tool_call", patch, T0 + timedelta(days=1, minutes=4))
    await _event(activity, "newer-session", 6, "tool_call",
                 {"type": "tool_use", "name": "Read", "input": {"file_path": f"{root}/r.py"}},
                 T0 + timedelta(days=1, minutes=5))
    await _event(activity, "newer-session", 7, "message", _say("now add a test"), T0 + timedelta(days=1, minutes=6))
    await _commit_link(activity, "newer-session", sha)
    await _commit_link(activity, "newer-session", "f" * 40)

    path = await update_context_file(root, store=_Store(), activity=activity)

    text = path.read_text()
    newer = text.index("2026-10-02 09:06 UTC · codex · `newer-se`")
    older = text.index("2026-10-01 09:00 UTC · claude-code · `older-se`")
    assert newer < older < text.index("## Recent decisions")
    assert "- first ask: fix the login bug\n- last ask: now add a test\n" in text
    assert "- files: `src/a.py`, `docs/b.md`\n" in text
    assert f"- commit {sha[:7]} feat: render recent sessions\n" in text
    assert "- commit fffffff\n" in text
    assert "boot" not in text and "r.py" not in text


async def test_the_file_never_passes_100_lines(activity, git_repo):
    for i in range(20):
        sid = f"s{i:02d}"
        await _session(activity, sid, workspace=str(git_repo))
        await _event(activity, sid, 0, "message", _say("very long ask " * 200), T0 + timedelta(hours=i))
        for j in range(40):
            await _event(activity, sid, j + 1, "tool_call", _edit(f"{git_repo}/f{j}.py"),
                         T0 + timedelta(hours=i, seconds=j))
        await _event(activity, sid, 99, "message", _say("last\n" * 50), T0 + timedelta(hours=i, minutes=5))
        for k in range(10):
            await _commit_link(activity, sid, f"{k:040d}")
    decisions = [Decision(id=f"dec-{n:03d}", timestamp=T0, agent="manual", repo="axon",
                          summary="d " * 100) for n in range(30)]
    lessons = [LessonHandle(id=uuid4(), kind="agent-error", triggers=["x"]) for _ in range(200)]

    async def catalog():
        return lessons

    file_bridge_catalog = file_bridge._lesson_catalog
    file_bridge._lesson_catalog = catalog
    try:
        path = await update_context_file(git_repo, store=_Store(decisions), activity=activity)
    finally:
        file_bridge._lesson_catalog = file_bridge_catalog

    lines = path.read_text().splitlines()
    assert len(lines) <= 100
    asks = [line for line in lines if line.startswith("- first ask:")]
    assert len(asks) == 3 and len(asks[0]) <= len("- first ask: ") + 160
    assert any(line.endswith("+32 more") for line in lines)
    assert "+7 more commit(s)" in "\n".join(lines)


async def test_a_repo_with_no_activity_still_gets_decisions(activity, git_repo):
    store = _Store([Decision(id="dec-007", timestamp=T0, agent="manual", repo="axon",
                             summary="keep it small")])

    path = await update_context_file(git_repo, store=store, activity=activity)

    text = path.read_text()
    assert "- `dec-007` (draft) — keep it small" in text
    assert "Recent sessions" not in text


async def test_a_database_that_is_down_leaves_the_previous_file(git_repo):
    target = git_repo / ".axon" / "context.md"
    target.parent.mkdir()
    target.write_text("previous\n")
    down = PostgresActivityRepository("postgresql://axon:axon@127.0.0.1:1/down")

    with pytest.raises(OSError):
        await update_context_file(git_repo, store=_Store(), activity=down)

    assert target.read_text() == "previous\n"


async def test_refresh_outside_a_git_repo_is_a_quiet_no_op(tmp_path):
    assert await refresh_context_for_cwd(tmp_path) is None


async def test_refresh_from_a_subdirectory_writes_the_repo_root_file(activity, git_repo):
    sub = git_repo / "src"
    sub.mkdir()

    path = await refresh_context_for_cwd(sub)

    assert path == git_repo / ".axon" / "context.md"
    assert path.read_text().startswith("# AXON context — axon")


async def test_regenerating_with_50_sessions_of_200_events_is_fast(activity, git_repo):
    pool = await activity._ensure_pool()
    async with pool.acquire() as con:
        await con.executemany(
            "INSERT INTO activity_sessions (session_id, harness, source_id, project, workspace,"
            " status, coverage) VALUES ($1, 'claude-code', $1, 'axon', $2, 'ended', 'full')",
            [(f"s{i}", str(git_repo)) for i in range(50)],
        )
        rows = []
        for i in range(50):
            for j in range(200):
                kind, content = (("message", _say(f"ask {j}")) if j % 4 == 0
                                 else ("tool_call", _edit(f"{git_repo}/f{j}.py")))
                rows.append((f"s{i}-{j}", f"s{i}", T0 + timedelta(hours=i, seconds=j), kind, content))
        await con.executemany(
            "INSERT INTO activity_events (event_id, harness, source_id, session_id, occurred_at,"
            " ingested_at, kind, content, coverage, redactions)"
            " VALUES ($1, 'claude-code', $1, $2, $3, $3, $4, $5, 'full', '[]')",
            rows,
        )

    start = time.perf_counter()
    path = await update_context_file(git_repo, store=_Store(), activity=activity)
    elapsed = time.perf_counter() - start

    print(f"context.md regeneration, 50 sessions x 200 events: {elapsed * 1000:.0f} ms")
    assert "`s49`" in path.read_text()
    assert elapsed < 1.0


def test_multiline_decision_summaries_still_fit_in_100_lines():
    decisions = [Decision(id=f"dec-{n:03d}", timestamp=T0, agent="manual", repo="axon",
                          summary="line\n" * 10) for n in range(15)]

    out = file_bridge._render("axon", decisions)

    assert len(out.splitlines()) <= 100
    assert "- `dec-000` (draft) — line line line" in out


def test_relative_and_dotted_paths_resolve_against_the_session_workspace(tmp_path):
    from axon.activity.models import RecentSession

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    s = RecentSession("s", "codex", str(root / "src"), T0, edit_calls=[
        {"name": "exec", "input": "*** Update File: a.py\\n"},
        _edit(f"{root}/src/a.py"),
        _edit(f"{root}/src/../src/a.py"),
    ])

    assert file_bridge._touched_files(s, root) == ["src/a.py"]


def test_a_codex_patch_lists_deleted_files_and_rename_targets(tmp_path):
    from axon.activity.models import RecentSession

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    patch = (
        "*** Begin Patch\\n*** Delete File: retired.py\\n"
        "*** Update File: old.py\\n*** Move to: new.py\\n@@\\n*** End Patch"
    )
    s = RecentSession("s", "codex", str(root), T0, edit_calls=[{"name": "exec", "input": patch}])

    assert file_bridge._touched_files(s, root) == ["retired.py", "old.py", "new.py"]


def test_files_touched_in_a_removed_worktree_are_relative_to_that_worktree(tmp_path):
    from axon.activity.models import RecentSession

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    gone = tmp_path / "worktrees" / "phase-x"  # removed after its branch merged
    s = RecentSession("s", "codex", str(gone), T0, edit_calls=[
        _edit(f"{gone}/src/a.py"),
        {"name": "exec", "input": "*** Update File: src/b.py\\n"},
    ])

    assert file_bridge._touched_files(s, root) == ["src/a.py", "src/b.py"]


def test_files_touched_in_a_worktree_inside_the_repo_drop_the_worktree_prefix(tmp_path):
    from axon.activity.models import RecentSession

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    wt = root / ".claude" / "worktrees" / "phase-x"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere\n")
    s = RecentSession("s", "claude-code", str(wt), T0, edit_calls=[_edit(f"{wt}/src/a.py")])

    assert file_bridge._touched_files(s, root) == ["src/a.py"]


def test_files_touched_in_a_removed_worktree_inside_the_repo_drop_its_prefix(tmp_path):
    from axon.activity.models import RecentSession

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    gone = root / ".claude" / "worktrees" / "phase-x"
    s = RecentSession("s", "claude-code", str(gone), T0, edit_calls=[_edit(f"{gone}/src/a.py")])

    assert file_bridge._touched_files(s, root) == ["src/a.py"]
