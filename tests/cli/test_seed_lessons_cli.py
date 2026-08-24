"""Issue #142: `axon seed-lessons` is the missing caller for `seed_corpus`.

Before this command existed, `seed_corpus` in `axon.lessons.seed` had no
caller anywhere in the CLI, the MCP server, or any hook - the corpus shipped
but production never seeded it. These tests exercise the command the same
way a user would invoke it (`CliRunner`), not `seed_corpus` directly (that is
already covered by `tests/mcp/test_lesson_seed.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import axon.lessons.seed as seed_module
from axon.cli import pb
from axon.mcp import server
from axon.models.lesson import LessonRecord
from axon.store.vector_common import VECTOR_SIZE

FIXTURE = Path(__file__).parent.parent / "fixtures" / "lessons" / "agent-errors.json"

runner = CliRunner()


class _FakeLessonStore:
    def __init__(self) -> None:
        self.saved: list[LessonRecord] = []
        self.inited = False

    async def init(self) -> None:
        self.inited = True

    async def close(self) -> None:
        pass

    async def insert(self, lesson: LessonRecord):
        self.saved.append(lesson)
        return lesson.id

    async def get_by_source(self, source: str) -> LessonRecord | None:
        return next((lesson for lesson in self.saved if lesson.source == source), None)

    async def update(self, lesson: LessonRecord) -> None:
        for i, existing in enumerate(self.saved):
            if existing.id == lesson.id:
                self.saved[i] = lesson
                return
        raise AssertionError(f"update() called for unknown id {lesson.id}")


class _FakeEngine:
    def __init__(self) -> None:
        self.calls = 0

    def embed_one(self, text: str) -> list[float]:
        self.calls += 1
        vector = [0.0] * VECTOR_SIZE
        vector[0] = 1.0
        return vector


def test_help_mentions_corpus_option() -> None:
    result = runner.invoke(pb.app, ["seed-lessons", "--help"])

    assert result.exit_code == 0
    assert "--corpus" in result.output


def test_defaults_to_the_shipped_corpus_path_when_corpus_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_store = _FakeLessonStore()
    fake_engine = _FakeEngine()
    captured: dict[str, Path] = {}

    async def fake_seed_corpus(path, record, *, store, engine) -> list[str]:
        captured["path"] = path
        return []

    monkeypatch.setattr(server, "_get_lesson_store", lambda: fake_store)
    monkeypatch.setattr(server, "_get_embedder", lambda: fake_engine)
    monkeypatch.setattr(seed_module, "seed_corpus", fake_seed_corpus)

    result = runner.invoke(pb.app, ["seed-lessons"])

    assert result.exit_code == 0, result.output
    assert captured["path"] == pb._DEFAULT_LESSON_CORPUS_PATH


def test_prints_one_status_line_per_corpus_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_store = _FakeLessonStore()
    fake_engine = _FakeEngine()
    monkeypatch.setattr(server, "_get_lesson_store", lambda: fake_store)
    monkeypatch.setattr(server, "_get_embedder", lambda: fake_engine)

    result = runner.invoke(pb.app, ["seed-lessons", "--corpus", str(FIXTURE)])

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(fake_store.saved) > 0, "fixture must not be empty, or this test proves nothing"
    assert len(lines) == len(fake_store.saved)
    for line in lines:
        assert line.startswith("recorded lesson ")


def test_second_run_reports_unchanged_and_issues_zero_embedding_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_store = _FakeLessonStore()
    fake_engine = _FakeEngine()
    monkeypatch.setattr(server, "_get_lesson_store", lambda: fake_store)
    monkeypatch.setattr(server, "_get_embedder", lambda: fake_engine)

    first = runner.invoke(pb.app, ["seed-lessons", "--corpus", str(FIXTURE)])
    assert first.exit_code == 0, first.output
    calls_after_first_run = fake_engine.calls
    assert calls_after_first_run > 0, "first run must have embedded something"

    second = runner.invoke(pb.app, ["seed-lessons", "--corpus", str(FIXTURE)])
    assert second.exit_code == 0, second.output

    lines = [line for line in second.output.splitlines() if line.strip()]
    assert len(lines) == len(fake_store.saved)
    for line in lines:
        assert line.endswith("unchanged, skipped.")

    assert fake_engine.calls == calls_after_first_run
