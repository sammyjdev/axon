"""Tests for repo identity resolution across MCP tools and call sites (Task 3)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from axon.core.decision import Decision
from axon.mcp import server
from axon.mcp.server import _resolve_repo, resolve_repo
from axon.store.session_store import SessionStore


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, check=True, capture_output=True  # noqa: S607
    )


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], path)
    _git(["config", "user.email", "test@axon.dev"], path)
    _git(["config", "user.name", "AXON Test"], path)
    (path / "README.md").write_text(f"# Test for {path.name}\n", encoding="utf-8")
    _git(["add", "."], path)
    _git(["commit", "-m", "init"], path)
    return path


def _get_pg_url() -> str:
    env = os.environ.get("AXON_PG_URL")
    if env:
        return env
    from axon.config.runtime import load_runtime_config

    return load_runtime_config().pg_url


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture(autouse=True)
def _use_test_store(store: SessionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_get_session_store", lambda: store)


# ---------------------------------------------------------------------------
# Resolver unit tests
# ---------------------------------------------------------------------------


def test_resolve_repo_absolute_path_normalises_to_repo_name(tmp_path: Path) -> None:
    repo_path = _init_repo(tmp_path / "sample_project")
    resolved = _resolve_repo(str(repo_path))
    assert resolved == "sample_project"
    assert resolve_repo(str(repo_path)) == "sample_project"


def test_resolve_repo_bare_name_unchanged_and_never_invokes_git(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("git was invoked for a bare name!")

    monkeypatch.setattr("subprocess.check_output", _fail)
    monkeypatch.setattr("subprocess.run", _fail)

    with caplog.at_level(logging.WARNING):
        assert _resolve_repo("axon") == "axon"
        assert _resolve_repo("arbitrary-repo") == "arbitrary-repo"

    assert len(caplog.records) == 0


def test_resolve_repo_dot_and_dotdot_normalise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = _init_repo(tmp_path / "root_repo")
    sub_dir = repo_dir / "nested" / "deep"
    sub_dir.mkdir(parents=True)

    monkeypatch.chdir(sub_dir)
    assert _resolve_repo(".") == "root_repo"
    assert _resolve_repo("..") == "root_repo"


def test_resolve_repo_none_falls_back_to_detect_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = _init_repo(tmp_path / "active_repo")
    monkeypatch.chdir(repo_dir)

    assert _resolve_repo(None) == "active_repo"
    assert _resolve_repo("") == "active_repo"
    assert _resolve_repo(None, allow_none=True) is None
    assert _resolve_repo("", allow_none=True) is None


def test_resolve_repo_non_repo_path_falls_back_to_basename(tmp_path: Path) -> None:
    plain_dir = tmp_path / "plain_dir"
    plain_dir.mkdir()
    assert _resolve_repo(str(plain_dir)) == "plain_dir"


# ---------------------------------------------------------------------------
# Call site wiring: one test per risk class + specific tools
# ---------------------------------------------------------------------------


async def test_wire_read_tool_axon_search_normalises_path(
    store: SessionStore, tmp_path: Path
) -> None:
    repo_dir = _init_repo(tmp_path / "search_target")
    decision = Decision(
        id=await store.next_decision_id(),
        timestamp=datetime.now(UTC),
        agent="manual",
        repo="search_target",
        summary="support query searching for read tool test",
        status="draft",
    )
    await store.save_decision(decision)

    out = await server.axon_search("support query", repo=str(repo_dir))
    assert decision.id in out
    assert "search_target" not in out or decision.summary in out


async def test_wire_read_tool_axon_get_context_normalises_path(
    store: SessionStore, tmp_path: Path
) -> None:
    repo_dir = _init_repo(tmp_path / "context_target")
    decision = Decision(
        id=await store.next_decision_id(),
        timestamp=datetime.now(UTC),
        agent="manual",
        repo="context_target",
        summary="recall context for read risk tool",
        status="draft",
    )
    await store.save_decision(decision)

    out = await server.axon_get_context(repo=str(repo_dir))
    assert "recall context for read risk tool" in out


async def test_wire_write_tool_axon_capture_normalises_path(
    store: SessionStore, tmp_path: Path
) -> None:
    repo_dir = _init_repo(tmp_path / "capture_target")
    out = await server.axon_capture(
        summary="captured decision under path",
        repo=str(repo_dir),
    )
    assert "captured" in out
    assert "for capture_target" in out

    decisions = await store.find_decisions_by_repo("capture_target")
    assert len(decisions) == 1
    assert decisions[0].repo == "capture_target"


async def test_wire_destructive_tool_axon_export_now_normalises_path(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    monkeypatch.setenv("AXON_VAULT", str(vault))
    monkeypatch.setenv("AXON_ALLOW_DESTRUCTIVE", "1")

    repo_dir = _init_repo(tmp_path / "export_target")
    decision = Decision(
        id=await store.next_decision_id(),
        timestamp=datetime.now(UTC),
        agent="manual",
        repo="export_target",
        summary="decision exported to architecture doc",
        status="draft",
    )
    await store.save_decision(decision)

    out = await server.axon_export_now(repo=str(repo_dir))
    assert "exported 1 decision(s) for export_target" in out

    arch_file = vault / "AXON" / "Architecture" / "export_target.md"
    assert arch_file.is_file()


async def test_wire_destructive_tool_axon_mark_done_normalises_path(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    monkeypatch.setenv("AXON_VAULT", str(vault))
    monkeypatch.setenv("AXON_ALLOW_DESTRUCTIVE", "1")

    repo_dir = _init_repo(tmp_path / "done_target")
    out = await server.axon_mark_done(repo=str(repo_dir))
    assert "export_target" not in out
    notes = await store.get_notes("done_target")
    assert any("[scope] marked done" in n.body for n in notes)


async def test_axon_validation_stats_normalises_path(
    store: SessionStore, tmp_path: Path
) -> None:
    repo_dir = _init_repo(tmp_path / "stats_target")
    await store.save_decision(
        Decision(
            id=await store.next_decision_id(),
            timestamp=datetime.now(UTC),
            agent="manual",
            repo="stats_target",
            summary="scored decision",
            validation_score=5.0,
            judged=True,
            status="draft",
        )
    )

    out = await server.axon_validation_stats(repo=str(repo_dir), threshold=3.5)
    data = json.loads(out)
    assert data["n_total"] == 1
    assert data["n_passed"] == 1


async def test_axon_validation_stats_none_aggregates_workspace(
    store: SessionStore,
) -> None:
    for i, (repo, score) in enumerate([("repo_a", 4.0), ("repo_b", 2.0)]):
        await store.save_decision(
            Decision(
                id=f"dec-{i+100:03d}",
                timestamp=datetime.now(UTC),
                agent="manual",
                repo=repo,
                summary=f"summary {i}",
                validation_score=score,
                judged=True,
                status="draft",
            )
        )

    out = await server.axon_validation_stats(repo=None, threshold=3.0)
    data = json.loads(out)
    assert data["n_total"] == 2
    assert data["n_passed"] == 1


async def test_axon_session_start_stores_repo_name_and_returns_nonempty_recall(
    store: SessionStore, tmp_path: Path
) -> None:
    repo_dir = _init_repo(tmp_path / "axon")
    decision = Decision(
        id=await store.next_decision_id(),
        timestamp=datetime.now(UTC),
        agent="manual",
        repo="axon",
        summary="recall seed for session start",
        status="draft",
    )
    await store.save_decision(decision)

    out = await server.axon_session_start(agent="codex", repo=str(repo_dir))
    assert "repo: axon" in out
    assert "recall seed for session start" in out

    session_id = out.split("session:")[1].split()[0]
    ended_repo = await store.end_session(session_id)
    assert ended_repo == "axon"

    con = await asyncpg.connect(_get_pg_url())
    try:
        row = await con.fetchrow("SELECT repo FROM sessions WHERE id = $1", session_id)
        assert row is not None
        assert row["repo"] == "axon"
    finally:
        await con.close()


async def test_wire_capture_event_normalises_path(
    store: SessionStore, tmp_path: Path
) -> None:
    repo_dir = _init_repo(tmp_path / "event_target")
    out = await server.axon_capture_event(
        "test_metric",
        {"repo": str(repo_dir), "passed": 12},
    )
    assert "captured test_metric for event_target" in out
    notes = await store.get_notes("event_target")
    assert len(notes) == 1
    assert notes[0].project == "event_target"


async def test_wire_record_outcome_normalises_path(
    tmp_path: Path,
) -> None:
    repo_dir = _init_repo(tmp_path / "outcome_target")
    out = await server.axon_record_outcome(
        summary="shipped feature",
        outcome="green",
        repo=str(repo_dir),
    )
    assert "for outcome_target" in out


async def test_wire_handoff_normalises_path(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    monkeypatch.setenv("AXON_VAULT", str(vault))

    repo_dir = _init_repo(tmp_path / "handoff_target")
    out = await server.axon_handoff(
        to_agent="codex",
        repo=str(repo_dir),
        notes="note for handoff",
    )
    assert "repo: handoff_target" in out
    assert "note for handoff" in out
