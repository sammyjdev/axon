from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer

from axon.activity.models import ActivityEvent, ActivitySession
from axon.activity.repository import PostgresActivityRepository
from axon.activity.why import explain
from tests.activity.test_commit_links import _git


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


class _Store:
    def __init__(self, decision=None) -> None:
        self._decision = decision

    async def find_decision_by_git_hash(self, git_hash, repo=None):
        return self._decision if self._decision and self._decision.git_hash == git_hash else None


@pytest.fixture
def work(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("a")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add a")
    return tmp_path


async def _message(repo, n: int, text: str | None, **extra) -> None:
    ts = datetime(2026, 10, 1, 10, n, tzinfo=UTC)
    content = {"text": text, **extra} if text is not None else dict(extra)
    await repo.upsert_event(
        ActivityEvent(
            event_id=f"m{n}",
            harness="claude-code",
            source_id=f"m{n}",
            session_id="s1",
            occurred_at=ts,
            ingested_at=ts,
            kind="message",
            content=content,
            coverage="full",
            redactions=[],
        )
    )


async def _session_with_commit(repo, work: Path) -> str:
    full = _git(work, "rev-parse", "HEAD")
    await repo.upsert_session(
        ActivitySession(
            session_id="s1",
            harness="claude-code",
            source_id="s1",
            project="axon",
            workspace=str(work),
            status="complete",
            coverage="full",
        )
    )
    await _message(repo, 1, "first typed", role="user")
    await _message(repo, 2, "second typed", role="user")
    await _message(repo, 3, "harness reminder", role="user", injected=True)
    await _message(repo, 4, "assistant reply", role="assistant")
    await _message(repo, 5, "third typed", role="user")
    await _message(repo, 6, "fourth typed", role="user")
    await _message(repo, 59, "typed after the commit", role="user")
    ts = datetime(2026, 10, 1, 10, 30, tzinfo=UTC)
    await repo.upsert_event(
        ActivityEvent(
            event_id="res",
            harness="claude-code",
            source_id="res",
            session_id="s1",
            occurred_at=ts,
            ingested_at=ts,
            kind="tool_result",
            content={},
            coverage="full",
            redactions=[],
        )
    )
    await repo.add_commit_link(commit=full, session_id="s1", event_id="res")
    return full


async def test_why_commit_shows_subject_session_and_the_last_three_typed_prompts(
    repo, work
) -> None:
    await _session_with_commit(repo, work)

    out = await explain(repo, _Store(), "HEAD", cwd=work)

    assert "add a" in out
    assert "session s1  claude-code  project axon  2026-10-01 10:30" in out
    prompts = [line.strip() for line in out.splitlines() if "prompt:" in line]
    assert prompts == ["prompt: second typed", "prompt: third typed", "prompt: fourth typed"]


async def test_why_prompts_tolerate_messages_without_role_or_injected_flag(repo, work) -> None:
    await _session_with_commit(repo, work)
    await _message(repo, 7, "legacy shape")

    out = await explain(repo, _Store(), "HEAD", cwd=work)

    assert "prompt: legacy shape" in out


async def test_why_commit_lists_the_decision_recorded_for_that_hash(repo, work) -> None:
    full = await _session_with_commit(repo, work)
    decision = SimpleNamespace(id="dec-7", summary="use X over Y", git_hash=full)

    out = await explain(repo, _Store(decision), "HEAD", cwd=work)

    assert "decision dec-7: use X over Y" in out


async def test_why_commit_without_a_linked_session_says_so(repo, work) -> None:
    out = await explain(repo, _Store(), "HEAD", cwd=work)

    assert "no session is linked to this commit" in out


async def test_why_file_walks_its_last_commits(repo, work) -> None:
    await _session_with_commit(repo, work)
    (work / "a.txt").write_text("b")
    _git(work, "commit", "-q", "-am", "change a")

    out = await explain(repo, _Store(), "a.txt", cwd=work)

    assert out.index("change a") < out.index("add a")
    assert out.count("no session is linked to this commit") == 1
    assert "session s1" in out
