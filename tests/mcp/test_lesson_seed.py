"""Wave D Task 11: seed the corpus that already exists.

Until this test exists and passes, ``~/.claude/agents/forge/lessons/agent-errors.json``
is data with no mechanism: nothing loads its entries through ``axon_record_lesson``.

The corpus lives outside this repo, in a different git repository, at an
absolute path under someone's home directory. A test that reads that path
directly would pass on this laptop and nowhere else - CI has no
``~/.claude``. So the corpus is vendored here as a fixture
(``tests/fixtures/lessons/agent-errors.json``, a byte-for-byte copy) and the
real round-trip test runs against the fixture unconditionally. A second,
best-effort test compares the fixture to the live file and is skipped
whenever the live file is absent - it exists to catch drift on the machine
that can see both, never to gate CI on a path CI cannot have.

Doubles for the store and the embedder are duplicated from
``test_lesson_tools.py`` rather than imported: that file is being edited
concurrently for Task 10, and importing test doubles from a file under
concurrent edit would couple this test's stability to unrelated changes
landing there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axon.lessons.seed import corpus_entry_to_lesson_kwargs, load_corpus, seed_corpus
from axon.mcp import server
from axon.models.lesson import LessonRecord
from axon.store.vector_common import VECTOR_SIZE

FIXTURE = Path(__file__).parent.parent / "fixtures" / "lessons" / "agent-errors.json"
LIVE_CORPUS = Path.home() / ".claude" / "agents" / "forge" / "lessons" / "agent-errors.json"


class _FakeLessonStore:
    def __init__(self) -> None:
        self.saved: list[LessonRecord] = []
        self.inited = False

    async def init(self) -> None:
        self.inited = True

    async def insert(self, lesson: LessonRecord):
        self.saved.append(lesson)
        return lesson.id


class _FakeEngine:
    def embed_one(self, text: str) -> list[float]:
        vector = [0.0] * VECTOR_SIZE
        vector[0] = 1.0
        return vector


@pytest.fixture
def lesson_store() -> _FakeLessonStore:
    return _FakeLessonStore()


@pytest.fixture(autouse=True)
def _use_test_doubles(
    lesson_store: _FakeLessonStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_get_lesson_store", lambda: lesson_store)
    monkeypatch.setattr(server, "_get_embedder", lambda: _FakeEngine())


async def test_every_corpus_entry_round_trips_through_axon_record_lesson(
    lesson_store: _FakeLessonStore,
) -> None:
    entries = load_corpus(FIXTURE)
    assert entries, "fixture must not be empty, or this test proves nothing"

    out = await seed_corpus(FIXTURE, server.axon_record_lesson)

    assert len(out) == len(entries)
    assert len(lesson_store.saved) == len(entries)

    by_source = {lesson.source: lesson for lesson in lesson_store.saved}
    for entry in entries:
        expected = corpus_entry_to_lesson_kwargs(entry, FIXTURE)
        saved = by_source[expected["source"]]
        assert saved.kind == "agent-error"
        assert saved.triggers == entry["triggers"]
        assert saved.mistake == entry["mistake"]
        assert saved.tell == entry["tell"]
        assert saved.fix == entry["fix"]
        assert saved.embedding is not None


@pytest.mark.skipif(
    not LIVE_CORPUS.exists(), reason="~/.claude/agents/forge is not on this machine"
)
def test_vendored_fixture_matches_the_live_corpus() -> None:
    assert FIXTURE.read_text() == LIVE_CORPUS.read_text(), (
        "the vendored fixture has drifted from the live corpus - copy "
        f"{LIVE_CORPUS} over {FIXTURE} and re-run"
    )
