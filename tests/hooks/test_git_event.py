"""Tests for AXON git event handlers (T3.2)."""

from __future__ import annotations

import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from axon.core.decision import SUMMARY_MAX_LEN
from axon.hooks.git_event import main, on_commit, on_init
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
    _git(["commit", "--allow-empty", "-m", "x" * 400], git_repo)
    decision_id = await on_commit(store=store, cwd=git_repo)
    found = [d for d in await store.find_decisions_by_repo("myrepo") if d.id == decision_id]
    assert len(found[0].summary) == SUMMARY_MAX_LEN


async def test_on_commit_keeps_a_subject_the_old_cap_would_have_cut(
    store: SessionStore, git_repo: Path
) -> None:
    """The slice and the field's limit are one constant now. They were two, both 80, and the
    hook path sliced before constructing - so this length was silently losing its tail on
    every commit whose subject ran past byte 80."""
    # rstrip: git strips trailing whitespace from a subject line, so the recorded value
    # would differ from the argument for a reason that has nothing to do with the cap.
    subject = ("fix: " + "the pack refills the slots dedup removes, " * 3).rstrip()
    assert 80 < len(subject) <= SUMMARY_MAX_LEN
    _git(["commit", "--allow-empty", "-m", subject], git_repo)
    decision_id = await on_commit(store=store, cwd=git_repo)
    found = [d for d in await store.find_decisions_by_repo("myrepo") if d.id == decision_id]
    assert found[0].summary == subject


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


def test_main_unknown_event_returns_zero() -> None:
    assert main(["bogus"]) == 0
    assert main([]) == 0


async def test_on_commit_enqueues_lesson_draft(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from axon.adr.lesson_pool import list_drafts

    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / "data-root" / ".axon"))
    repo = tmp_path / "lessonrepo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "test@axon.dev"], repo)
    _git(["config", "user.name", "AXON Test"], repo)
    (repo / "client.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(
        [
            "commit",
            "-m",
            "fix: retry the flaky client",
            "-m",
            "Lesson: retries without jitter thundering-herd the upstream",
        ],
        repo,
    )

    await on_commit(store=store, cwd=repo)

    drafts = list_drafts(draft_dir=tmp_path / "data-root" / ".axon" / "lesson-draft")
    assert len(drafts) == 1
    assert drafts[0].title == "retries without jitter thundering-herd the upstream"
    assert "client.py" in drafts[0].context


async def test_on_commit_without_lesson_trailer_enqueues_nothing(
    store: SessionStore, git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from axon.adr.lesson_pool import list_drafts

    monkeypatch.setenv("AXON_DATA_ROOT", str(git_repo / ".axon"))
    await on_commit(store=store, cwd=git_repo)
    assert list_drafts(draft_dir=git_repo / ".axon" / "lesson-draft") == []


async def test_on_commit_idempotent_skip_does_not_duplicate_lesson_draft(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from axon.adr.lesson_pool import list_drafts

    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))
    repo = tmp_path / "lessonrepo2"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "test@axon.dev"], repo)
    _git(["config", "user.name", "AXON Test"], repo)
    (repo / "client.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(
        ["commit", "-m", "fix: retry", "-m", "Lesson: retries need jitter"],
        repo,
    )

    await on_commit(store=store, cwd=repo)
    await on_commit(store=store, cwd=repo)  # idempotent skip path

    drafts = list_drafts(draft_dir=tmp_path / ".axon" / "lesson-draft")
    assert len(drafts) == 1
