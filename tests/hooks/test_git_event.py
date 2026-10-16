"""Tests for AXON git event handlers (T3.2)."""

from __future__ import annotations

import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from axon.core.decision import SUMMARY_MAX_LEN
from axon.hooks.git_event import main, on_commit
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
    _git(["commit", "-m", "decision: add the entry point"], repo)
    return repo


async def test_a_plain_commit_is_not_captured(store: SessionStore, tmp_path: Path) -> None:
    repo = tmp_path / "plainrepo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "test@axon.dev"], repo)
    _git(["config", "user.name", "AXON Test"], repo)
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-m", "feat: add the entry point"], repo)

    assert await on_commit(store=store, cwd=repo) is None
    assert await store.find_decisions_by_repo("plainrepo") == []
    assert not (repo / ".axon" / "context.md").exists()


async def test_a_decision_trailer_commit_is_captured(
    store: SessionStore, git_repo: Path
) -> None:
    _git(
        ["commit", "--allow-empty", "-m", "refactor: split the store",
         "-m", "ADR-Decision: one store per table"],
        git_repo,
    )
    decision_id = await on_commit(store=store, cwd=git_repo)
    found = [d for d in await store.find_decisions_by_repo("myrepo") if d.id == decision_id]
    # The trailer names the decision; the subject only names the change.
    assert found[0].summary == "one store per table"


async def test_on_commit_captures_draft_decision(
    store: SessionStore, git_repo: Path
) -> None:
    decision_id = await on_commit(store=store, cwd=git_repo)
    assert decision_id == "dec-001"

    found = await store.find_decisions_by_repo("myrepo")
    assert len(found) == 1
    decision = found[0]
    assert decision.summary == "decision: add the entry point"
    assert decision.status == "draft"
    assert decision.agent == "manual"
    assert Path("main.py") in decision.files
    assert decision.git_hash is not None and len(decision.git_hash) == 40


async def test_on_commit_truncates_long_subject(
    store: SessionStore, git_repo: Path
) -> None:
    _git(["commit", "--allow-empty", "-m", "decision: " + "x" * 400], git_repo)
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
    subject = ("decision: " + "the pack refills the slots dedup removes, " * 3).rstrip()
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


def _init_repo(path: Path) -> None:
    path.mkdir()
    _git(["init"], path)
    _git(["config", "user.email", "test@axon.dev"], path)
    _git(["config", "user.name", "AXON Test"], path)
    (path / "f.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "."], path)
    _git(["commit", "-m", "init"], path)


async def test_a_lesson_trailer_alone_records_a_draft_and_no_decision(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from axon.adr.lesson_pool import list_drafts

    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))
    repo = tmp_path / "lessononly"
    _init_repo(repo)
    _git(["commit", "--allow-empty", "-m", "fix: retry", "-m", "Lesson: retries need jitter"], repo)

    assert await on_commit(store=store, cwd=repo) is None
    assert await store.find_decisions_by_repo("lessononly") == []
    assert [d.title for d in list_drafts(draft_dir=tmp_path / ".axon" / "lesson-draft")] == [
        "retries need jitter"
    ]


async def test_a_lesson_draft_edited_by_hand_survives_a_second_run(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))
    repo = tmp_path / "lessonedit"
    _init_repo(repo)
    _git(["commit", "--allow-empty", "-m", "fix: retry", "-m", "Lesson: retries need jitter"], repo)
    await on_commit(store=store, cwd=repo)
    (draft,) = (tmp_path / ".axon" / "lesson-draft").glob("*.md")
    draft.write_text(draft.read_text(encoding="utf-8") + "\nedited by hand\n", encoding="utf-8")

    await on_commit(store=store, cwd=repo)

    assert "edited by hand" in draft.read_text(encoding="utf-8")
