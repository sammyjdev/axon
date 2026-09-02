"""The catalogue reaches an agent through `.axon/context.md`, or it reaches nobody.

The reason lessons go unread is not that they are hard to fetch - `search_lessons` is one
call - it is that nothing tells an agent they exist. Measured over the 30 days to
2026-09-01: 22 of 540 sessions called `search_lessons` at all (4.1%), and only 6 of 337 main
sessions (1.8%). The subagent rate is higher (7.9%) for the one reason that supports this
design: FORGE's briefs name the call. Being told beats being able.

So the catalogue is a list of handles, and every rendering of it names the call that turns a
handle into a lesson. A list with no way to act on it is a phone book with no telephone.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from axon.hooks.file_bridge import _render, update_context_file
from axon.store.lessons import LessonHandle


class _StoreWithNoDecisions:
    async def find_decisions_by_repo(self, repo, limit=None):
        return []


def _handle(kind="agent-error", triggers=("shell-script",)):
    return LessonHandle(id=uuid4(), kind=kind, triggers=list(triggers))


def test_the_section_names_the_call_that_retrieves_a_lesson():
    out = _render("axon", [], lessons=[_handle()])
    assert "search_lessons" in out, (
        "the catalogue lists lessons without naming the call that fetches one")


def test_a_line_carries_the_triggers_that_decide_whether_to_ask():
    out = _render("axon", [], lessons=[_handle(triggers=["shell-script", "heredoc"])])
    assert "shell-script" in out and "heredoc" in out


def test_no_lessons_means_no_section_at_all():
    """An empty heading is worse than nothing: it spends the tokens and teaches the reader
    that the section is usually empty, which is how a section stops being read."""
    out = _render("axon", [], lessons=[])
    assert "Lessons" not in out
    assert "search_lessons" not in out


def test_the_catalogue_is_capped_so_a_growing_corpus_cannot_eat_the_context():
    """This text is prepended to every session. The corpus grows monotonically - lessons are
    added, never retired - so an uncapped list is a slow leak into every session's budget."""
    out = _render("axon", [], lessons=[_handle() for _ in range(200)])
    assert len(out.splitlines()) < 80, "the catalogue grew without a ceiling"


@pytest.mark.asyncio
async def test_a_machine_without_the_lessons_dsn_still_gets_its_context_file(tmp_path,
                                                                            monkeypatch):
    """`resolve_lessons_dsn()` raises RuntimeError with no fallback, on purpose: a missing
    `AXON_LESSONS_PG_URL` must refuse rather than quietly put one client's lessons in the
    database every other store shares.

    That refusal is correct where it lives and fatal here. This file is written on git
    events and at session end, and a SessionStart hook cats it; letting the exception out
    would trade "no lesson catalogue" for "no context file at all", on every machine that
    never set the variable. The catalogue is the optional part."""
    monkeypatch.delenv("AXON_LESSONS_PG_URL", raising=False)
    store = _StoreWithNoDecisions()

    path = await update_context_file(tmp_path, store=store)

    assert path.is_file()
    text = path.read_text()
    assert "AXON context" in text
    assert "Lessons" not in text, "the section appeared with no database to build it from"
