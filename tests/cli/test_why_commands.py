"""`axon activity link-commits` and `axon why` through the real CLI entry point."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from typer.testing import CliRunner

pytest.importorskip("testcontainers.postgres")
import asyncpg  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.__main__ import app as cli_app  # noqa: E402
from axon.activity.repository import PostgresActivityRepository  # noqa: E402
from axon.cli import pb  # noqa: E402
from tests.activity.test_commit_links import _git, _seed  # noqa: E402

runner = CliRunner()


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
def git_repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("a")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "first change")
    return tmp_path


@pytest.fixture(autouse=True)
def dsn(pg_dsn, monkeypatch):
    async def _reset() -> None:
        con = await asyncpg.connect(pg_dsn)
        await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        await con.close()

    asyncio.run(_reset())
    monkeypatch.setattr(pb, "_RUNTIME", replace(pb._RUNTIME, pg_url=pg_dsn))
    monkeypatch.setenv("AXON_PG_URL", pg_dsn)
    return pg_dsn


def _seed_session(dsn: str, *args, **kwargs) -> None:
    # The CLI runs its own event loop, so the tests are sync and seed in a loop of their own.
    async def _run() -> None:
        repo = PostgresActivityRepository(dsn)
        await repo.ensure_schema()
        try:
            await _seed(repo, *args, **kwargs)
        finally:
            await repo.close()

    asyncio.run(_run())


def test_link_commits_all_reports_counts_and_adds_nothing_the_second_time(dsn, git_repo) -> None:
    # Given two stored sessions, one with a commit and one whose commit failed
    short = _git(git_repo, "rev-parse", "--short", "HEAD")
    _seed_session(dsn, "s1", str(git_repo), short)
    _seed_session(dsn, "s2", str(git_repo), short, ok=False)

    # When link-commits --all runs twice
    first = runner.invoke(cli_app, ["activity", "link-commits", "--all"])
    second = runner.invoke(cli_app, ["activity", "link-commits", "--all"])

    # Then the first run links one commit and the second links none
    assert first.exit_code == 0, first.output
    assert "sessions: 2  links added: 1" in first.output
    assert "sessions: 2  links added: 0" in second.output


def test_link_commits_for_one_session_only_scans_that_session(dsn, git_repo) -> None:
    short = _git(git_repo, "rev-parse", "--short", "HEAD")
    _seed_session(dsn, "s1", str(git_repo), short)

    result = runner.invoke(cli_app, ["activity", "link-commits", "--session", "s1"])

    assert result.exit_code == 0, result.output
    assert "sessions: 1  links added: 1" in result.output


def test_link_commits_needs_a_session_or_all() -> None:
    result = runner.invoke(cli_app, ["activity", "link-commits"])

    assert result.exit_code != 0


def test_why_names_the_session_that_produced_the_commit(dsn, git_repo, monkeypatch) -> None:
    # Given a session whose commit was linked
    short = _git(git_repo, "rev-parse", "--short", "HEAD")
    _seed_session(dsn, "s1", str(git_repo), short)
    runner.invoke(cli_app, ["activity", "link-commits", "--session", "s1"])
    monkeypatch.chdir(git_repo)

    # When `axon why a.txt` runs inside the repo
    result = runner.invoke(cli_app, ["why", "a.txt"])

    # Then it shows the commit subject and the session
    assert result.exit_code == 0, result.output
    assert "first change" in result.output
    assert "session s1  claude-code  project p" in result.output


def test_why_on_an_unknown_commit_fails_cleanly(git_repo, monkeypatch) -> None:
    monkeypatch.chdir(git_repo)

    result = runner.invoke(cli_app, ["why", "deadbeefdead"])

    assert result.exit_code == 1
    assert "axon why:" in result.output
