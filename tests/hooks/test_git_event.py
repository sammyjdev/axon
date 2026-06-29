"""Tests for AXON git event handlers (T3.2)."""

from __future__ import annotations

import subprocess
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from axon.core.decision import Decision
from axon.hooks.git_event import main, on_commit, on_init, on_push
from axon.store.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "test@axon.dev"], repo)
    _git(["config", "user.name", "AXON Test"], repo)
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-m", "feat: add the entry point"], repo)
    return repo


async def test_on_commit_captures_draft_decision(
    store: SessionStore, git_repo: Path
) -> None:
    decision_id = await on_commit(store=store, cwd=git_repo)
    assert decision_id == "dec-001"

    found = await store.find_decisions_by_repo("myrepo")
    assert len(found) == 1
    decision = found[0]
    assert decision.summary == "feat: add the entry point"
    assert decision.status == "draft"
    assert decision.agent == "manual"
    assert Path("main.py") in decision.files
    assert decision.git_hash is not None and len(decision.git_hash) == 40


async def test_on_commit_truncates_long_subject(
    store: SessionStore, git_repo: Path
) -> None:
    _git(["commit", "--allow-empty", "-m", "x" * 120], git_repo)
    decision_id = await on_commit(store=store, cwd=git_repo)
    found = [d for d in await store.find_decisions_by_repo("myrepo") if d.id == decision_id]
    assert len(found[0].summary) == 80


async def test_on_commit_detects_agent_from_env(
    store: SessionStore, git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_AGENT", "codex")
    decision_id = await on_commit(store=store, cwd=git_repo)
    found = [d for d in await store.find_decisions_by_repo("myrepo") if d.id == decision_id]
    assert found[0].agent == "codex"


async def test_on_commit_rejects_unknown_agent(
    store: SessionStore, git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_AGENT", "some-bot")
    await on_commit(store=store, cwd=git_repo)
    found = await store.find_decisions_by_repo("myrepo")
    assert found[0].agent == "manual"  # unknown agent falls back to manual


async def test_on_commit_links_touched_symbols(
    store: SessionStore, tmp_path: Path
) -> None:
    repo = tmp_path / "linkrepo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "test@axon.dev"], repo)
    _git(["config", "user.name", "AXON Test"], repo)
    (repo / "mod.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-m", "feat: add alpha"], repo)

    decision_id = await on_commit(store=store, cwd=repo)
    assert decision_id is not None

    subgraph = await store.query_subgraph(decision_id, depth=1)
    assert "alpha" in subgraph["nodes"]
    assert {"source": decision_id, "target": "alpha", "type": "touches"} in subgraph[
        "edges"
    ]
    node = await store.get_node("alpha")
    assert node is not None and node["type"] == "symbol"


async def test_on_init_is_a_safe_stub(store: SessionStore) -> None:
    assert await on_init(store=store) is None


async def test_on_commit_is_idempotent_for_same_sha(
    store: SessionStore, git_repo: Path
) -> None:
    first_id = await on_commit(store=store, cwd=git_repo)
    second_id = await on_commit(store=store, cwd=git_repo)

    assert first_id is not None
    assert second_id == first_id
    found = await store.find_decisions_by_repo("myrepo")
    assert len(found) == 1


async def test_on_commit_idempotent_skip_regenerates_context_md(
    store: SessionStore, git_repo: Path
) -> None:
    await on_commit(store=store, cwd=git_repo)
    context_md = git_repo / ".axon" / "context.md"
    assert context_md.exists()
    context_md.unlink()  # simulate stale/missing mirror

    await on_commit(store=store, cwd=git_repo)

    assert context_md.exists(), "idempotent skip should regenerate .axon/context.md"


async def test_on_commit_idempotent_skip_updates_agent_when_changed(
    store: SessionStore, git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AXON_AGENT", raising=False)
    decision_id = await on_commit(store=store, cwd=git_repo)
    found = (await store.find_decisions_by_repo("myrepo"))[0]
    assert found.agent == "manual"

    monkeypatch.setenv("AXON_AGENT", "claude-code")
    second_id = await on_commit(store=store, cwd=git_repo)
    assert second_id == decision_id
    found = (await store.find_decisions_by_repo("myrepo"))[0]
    assert found.agent == "claude-code"


async def test_on_commit_idempotent_skip_does_not_duplicate_edges(
    store: SessionStore, tmp_path: Path
) -> None:
    repo = tmp_path / "edgesrepo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "test@axon.dev"], repo)
    _git(["config", "user.name", "AXON Test"], repo)
    (repo / "mod.py").write_text("def gamma():\n    return 3\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-m", "feat: add gamma"], repo)

    decision_id = await on_commit(store=store, cwd=repo)
    assert decision_id is not None

    # second invocation re-runs the linker on the existing decision
    await on_commit(store=store, cwd=repo)

    subgraph = await store.query_subgraph(decision_id, depth=1)
    touch_edges = [
        e for e in subgraph["edges"] if e["type"] == "touches" and e["target"] == "gamma"
    ]
    assert len(touch_edges) == 1, (
        f"expected 1 touches edge after retry, got {len(touch_edges)}: {touch_edges}"
    )


async def test_on_commit_relinks_symbols_after_partial_failure(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "resumerepo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "test@axon.dev"], repo)
    _git(["config", "user.name", "AXON Test"], repo)
    (repo / "mod.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-m", "feat: add beta"], repo)

    calls = {"n": 0}
    _git_event_mod = __import__("axon.hooks.git_event", fromlist=["_link_touched_symbols"])
    original = _git_event_mod._link_touched_symbols

    async def flaky_link(store, decision_id, root, commit_hash):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("graph unavailable")
        return await original(store, decision_id, root, commit_hash)

    monkeypatch.setattr("axon.hooks.git_event._link_touched_symbols", flaky_link)

    with pytest.raises(RuntimeError):
        await on_commit(store=store, cwd=repo)

    # second run with same SHA must NOT duplicate the Decision and must
    # complete the linking step that previously failed.
    decision_id = await on_commit(store=store, cwd=repo)
    assert decision_id is not None

    found = await store.find_decisions_by_repo("resumerepo")
    assert len(found) == 1
    subgraph = await store.query_subgraph(decision_id, depth=1)
    assert "beta" in subgraph["nodes"]


def _init_repo(path: Path) -> None:
    path.mkdir()
    _git(["init"], path)
    _git(["config", "user.email", "test@axon.dev"], path)
    _git(["config", "user.name", "AXON Test"], path)
    (path / "f.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "."], path)
    _git(["commit", "-m", "init"], path)


async def test_on_push_exports_when_scope_ends(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "pushrepo"
    _init_repo(repo)
    _git(["tag", "v1.0"], repo)  # git-tag scope-end signal

    await store.save_decision(
        Decision(
            id="dec-001",
            timestamp=datetime(2026, 5, 1, tzinfo=UTC),
            agent="manual",
            repo="pushrepo",
            summary="a decision",
        )
    )
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)

    async def no_judge(decision: Decision, context: str = "") -> None:
        return None

    monkeypatch.setattr("axon.hooks.git_event.discover_vault", lambda: vault)
    monkeypatch.setattr("axon.hooks.git_event.score_decision", no_judge)

    await on_push(store=store, cwd=repo)

    assert (vault / "AXON" / "Architecture" / "pushrepo.md").exists()
    assert (vault / "AXON" / "Decisions" / "dec-001.md").exists()


async def test_judge_emits_validation_result_trace_per_scored_decision(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from axon.hooks import git_event
    from axon.observability.trace_store import TraceStore

    repo = tmp_path / "judgerepo"
    _init_repo(repo)
    _git(["tag", "v1.0"], repo)

    await store.save_decision(
        Decision(
            id="dec-001",
            timestamp=datetime(2026, 5, 1, tzinfo=UTC),
            agent="manual",
            repo="judgerepo",
            summary="a",
        )
    )
    await store.save_decision(
        Decision(
            id="dec-002",
            timestamp=datetime(2026, 5, 2, tzinfo=UTC),
            agent="manual",
            repo="judgerepo",
            summary="b",
        )
    )
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)

    fixed_scores = {"dec-001": 4.5, "dec-002": 2.0}
    judge_calls: list[str] = []

    async def fake_judge(decision: Decision, context: str = "") -> float:
        judge_calls.append(decision.id)
        return fixed_scores[decision.id]

    trace_store = TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "traces"))
    monkeypatch.setattr(git_event, "_TRACE_STORE", trace_store, raising=False)
    monkeypatch.setattr("axon.hooks.git_event.discover_vault", lambda: vault)
    monkeypatch.setattr("axon.hooks.git_event.score_decision", fake_judge)

    await on_push(store=store, cwd=repo)

    records = [r for r in trace_store.load_all() if r.stage == "validation_result"]
    assert len(records) == 2
    ids = {r.payload["decision_id"] for r in records}
    assert ids == {"dec-001", "dec-002"}
    passed_flags = {r.payload["decision_id"]: r.payload["passed"] for r in records}
    assert passed_flags["dec-001"] is True
    assert passed_flags["dec-002"] is False

    # Re-running on_push must NOT re-judge already-judged decisions
    # (previously gated on validation_score == 0.0, which mis-classified
    # any legitimate 0.0 score as unjudged).
    judge_calls.clear()
    await on_push(store=store, cwd=repo)
    assert judge_calls == []


async def test_judge_does_not_rejudge_when_score_legitimately_zero(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "zerorepo"
    _init_repo(repo)
    _git(["tag", "v1.0"], repo)

    await store.save_decision(
        Decision(
            id="dec-001",
            timestamp=datetime(2026, 5, 1, tzinfo=UTC),
            agent="manual",
            repo="zerorepo",
            summary="bad decision",
        )
    )
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)

    call_count = {"n": 0}

    async def judge_zero(decision: Decision, context: str = "") -> float:
        call_count["n"] += 1
        return 0.0  # legitimate clamped-to-zero score

    monkeypatch.setattr("axon.hooks.git_event.discover_vault", lambda: vault)
    monkeypatch.setattr("axon.hooks.git_event.score_decision", judge_zero)

    await on_push(store=store, cwd=repo)
    assert call_count["n"] == 1

    # second push must not re-judge: judged flag persists
    await on_push(store=store, cwd=repo)
    assert call_count["n"] == 1, "0.0 score with judged=True should not re-judge"


async def test_on_push_skips_export_when_scope_open(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "openrepo"
    _init_repo(repo)  # no tag, no milestone, no decisions

    calls: list[int] = []
    monkeypatch.setattr(
        "axon.hooks.git_event.discover_vault", lambda: calls.append(1)
    )
    await on_push(store=store, cwd=repo)
    assert calls == []


def test_main_unknown_event_returns_zero() -> None:
    assert main(["bogus"]) == 0
    assert main([]) == 0
