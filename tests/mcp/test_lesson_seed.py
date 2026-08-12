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

import json
import subprocess
from pathlib import Path

import pytest

from axon.lessons.seed import corpus_entry_to_lesson_kwargs, load_corpus, seed_corpus
from axon.mcp import server
from axon.models.lesson import LessonRecord
from axon.store.vector_common import VECTOR_SIZE

FIXTURE = Path(__file__).parent.parent / "fixtures" / "lessons" / "agent-errors.json"
CORPUS_REPO = Path.home() / ".claude"
CORPUS_PATH_IN_REPO = "agents/forge/lessons/agent-errors.json"
CORPUS_REF = "main"


def _corpus_at_stable_ref() -> str | None:
    """Read the corpus from ``main``, not from that repo's working tree.

    The working tree shows whichever branch the other repository happens to be
    checked out on. Reading it made this gate fail whenever someone switched
    branches there - a real occurrence, and one with nothing to do with this
    repo. ``main`` is the shared answer to "what is the corpus", so drift means
    the fixture and the published corpus disagree, not that a checkout moved.

    Returns None when the repo, the ref or the file is absent, so the test
    skips instead of failing on a machine that simply does not have it.
    """
    if not (CORPUS_REPO / ".git").exists():
        return None
    try:
        # S603/S607: the argv is a literal plus two module constants, and `git`
        # is resolved from PATH on purpose - no untrusted input reaches it.
        done = subprocess.run(  # noqa: S603
            ["git", "-C", str(CORPUS_REPO), "show", f"{CORPUS_REF}:{CORPUS_PATH_IN_REPO}"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


class _FakeLessonStore:
    def __init__(self) -> None:
        self.saved: list[LessonRecord] = []
        self.inited = False

    async def init(self) -> None:
        self.inited = True

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


@pytest.fixture
def lesson_store() -> _FakeLessonStore:
    return _FakeLessonStore()


@pytest.fixture
def engine() -> _FakeEngine:
    return _FakeEngine()


@pytest.fixture(autouse=True)
def _use_test_doubles(
    lesson_store: _FakeLessonStore, engine: _FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_get_lesson_store", lambda: lesson_store)
    monkeypatch.setattr(server, "_get_embedder", lambda: engine)


async def _seed(lesson_store: _FakeLessonStore, engine: _FakeEngine) -> list[str]:
    return await seed_corpus(FIXTURE, server.axon_record_lesson, store=lesson_store, engine=engine)


async def test_every_corpus_entry_round_trips_through_axon_record_lesson(
    lesson_store: _FakeLessonStore, engine: _FakeEngine
) -> None:
    entries = load_corpus(FIXTURE)
    assert entries, "fixture must not be empty, or this test proves nothing"

    out = await _seed(lesson_store, engine)

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


async def test_seeding_twice_does_not_duplicate(
    lesson_store: _FakeLessonStore, engine: _FakeEngine
) -> None:
    entries = load_corpus(FIXTURE)

    await _seed(lesson_store, engine)
    await _seed(lesson_store, engine)

    assert len(lesson_store.saved) == len(entries)


async def test_seeding_changed_content_updates_the_stored_record(
    lesson_store: _FakeLessonStore, engine: _FakeEngine, tmp_path: Path
) -> None:
    await _seed(lesson_store, engine)

    data = json.loads(FIXTURE.read_text())
    changed_id = data["lessons"][0]["id"]
    data["lessons"][0]["fix"] = "a brand new fix that was not in the original corpus"
    corpus_copy = tmp_path / FIXTURE.name
    corpus_copy.write_text(json.dumps(data))

    out = await seed_corpus(
        corpus_copy, server.axon_record_lesson, store=lesson_store, engine=engine
    )

    source = f"{FIXTURE.name}#{changed_id}"
    updated = await lesson_store.get_by_source(source)
    assert updated is not None
    assert updated.fix == "a brand new fix that was not in the original corpus"
    assert len(lesson_store.saved) == len(data["lessons"]), "must update in place, not insert"
    assert out[0] == f"updated lesson {updated.id} (agent-error)."


async def test_seeding_unchanged_content_does_not_reembed(
    lesson_store: _FakeLessonStore, engine: _FakeEngine
) -> None:
    await _seed(lesson_store, engine)
    calls_after_first_run = engine.calls

    await _seed(lesson_store, engine)

    assert calls_after_first_run > 0, (
        "first run must have embedded something, or this proves nothing"
    )
    assert engine.calls == calls_after_first_run


def test_vendored_fixture_matches_the_published_corpus() -> None:
    published = _corpus_at_stable_ref()
    if published is None:
        pytest.skip(f"{CORPUS_PATH_IN_REPO} is not readable at {CORPUS_REF} on this machine")

    assert FIXTURE.read_text() == published, (
        f"the vendored fixture has drifted from {CORPUS_REF}:{CORPUS_PATH_IN_REPO} - "
        f"run `git -C {CORPUS_REPO} show {CORPUS_REF}:{CORPUS_PATH_IN_REPO} > {FIXTURE}`. "
        "If the corpus has entries not yet merged to "
        f"{CORPUS_REF}, the fixture is ahead on purpose and should wait for that merge."
    )
