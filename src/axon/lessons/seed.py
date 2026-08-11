"""Seed the shipped agent-error corpus through ``axon_record_lesson``.

The corpus (``~/.claude/agents/forge/lessons/agent-errors.json``) predates the
lesson node and uses its own schema: ``id``, ``triggers``, ``mistake``,
``tell``, ``fix``, ``seen``. ``LessonCreate`` wants ``kind``, ``triggers``,
``mistake``, ``tell``, ``fix``, ``source``. Three fields do not line up, and
each is a decision, not a rename:

* ``kind`` -> always ``"agent-error"``. The corpus's own ``_comment`` says
  every entry here is an orchestrator mistake, not a language trap - those
  live in ``craft-lessons.md`` instead. There is nothing per-entry to decide.
* ``source`` -> ``"<corpus file name>#<corpus id>"``, e.g.
  ``"agent-errors.json#bash32-no-mapfile"``. The corpus's own ``id`` is the
  stable, human-meaningful handle; recording only the id would still leave
  "which corpus" ambiguous once a second file exists, so both travel
  together in the one field ``LessonCreate`` actually has.
* ``seen`` -> dropped. It is a date, sometimes with a place, describing when
  the ORIGINAL mistake happened - not this record's provenance and not this
  record's creation time (``LessonRecord.created_at`` already covers that).
  Overloading ``source`` with it would make the field answer two different
  questions; anyone who wants the original date has the corpus file, which
  is the source of truth for that.

Idempotency: enforced here, not with a database constraint. A UNIQUE index
on ``source`` would be wrong - ``axon_record_lesson`` accepts an arbitrary
``source`` from any caller, so two agents legitimately recording different
lessons under the same free-text ``source`` would collide on it. Instead,
``seed_corpus`` looks up each entry by its own stable ``file#id`` source key
before deciding what to do:

* No existing row -> insert, through ``axon_record_lesson`` (embeds once).
* An existing row whose content differs -> re-embed and overwrite it in
  place (same id, same ``created_at``): the corpus is the source of truth,
  so a corrected entry corrects the stored lesson too.
* An existing row whose content is identical -> skip. No write, and no
  embedding call - re-embedding unchanged rows would cost a network call
  per entry, every run, for nothing.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from axon.embedder.lesson_embedding import SupportsEmbedOne, embed_lesson
from axon.models.lesson import LessonCreate, LessonRecord
from axon.store.lessons import LessonStore

CorpusEntry = dict[str, Any]


def load_corpus(path: Path) -> list[CorpusEntry]:
    """Return the ``lessons`` list from a corpus file at ``path``."""
    data = json.loads(path.read_text())
    return data["lessons"]


def corpus_entry_to_lesson_kwargs(entry: CorpusEntry, corpus_path: Path) -> dict[str, Any]:
    """Map one corpus entry onto ``axon_record_lesson`` keyword arguments."""
    return {
        "kind": "agent-error",
        "triggers": entry["triggers"],
        "mistake": entry["mistake"],
        "tell": entry["tell"],
        "fix": entry["fix"],
        "source": f"{corpus_path.name}#{entry['id']}",
    }


def _unchanged(existing: LessonRecord, kwargs: dict[str, Any]) -> bool:
    """True if ``kwargs`` (a corpus entry's fields) match the stored row.

    ``source`` is the lookup key, already equal by construction.
    """
    return (
        existing.kind == kwargs["kind"]
        and existing.triggers == kwargs["triggers"]
        and existing.mistake == kwargs["mistake"]
        and existing.tell == kwargs["tell"]
        and existing.fix == kwargs["fix"]
    )


async def seed_corpus(
    path: Path,
    record: Callable[..., Awaitable[str]],
    *,
    store: LessonStore,
    engine: SupportsEmbedOne,
) -> list[str]:
    """Upsert each entry of the corpus at ``path`` into ``store``, by ``source``.

    New entries go through ``record`` (``axon_record_lesson``), which embeds
    and inserts. Existing entries are looked up directly in ``store``: an
    unchanged one is skipped (no write, no embedding call); a changed one is
    re-embedded and overwritten in place. Returns one status string per
    entry, in file order.
    """
    results = []
    for entry in load_corpus(path):
        kwargs = corpus_entry_to_lesson_kwargs(entry, path)
        existing = await store.get_by_source(kwargs["source"])

        if existing is None:
            results.append(await record(**kwargs))
            continue

        if _unchanged(existing, kwargs):
            results.append(f"lesson {existing.id} unchanged, skipped.")
            continue

        embedding = embed_lesson(LessonCreate(**kwargs), engine=engine)
        updated = LessonRecord(
            **kwargs, id=existing.id, created_at=existing.created_at, embedding=embedding
        )
        await store.update(updated)
        results.append(f"updated lesson {existing.id} ({kwargs['kind']}).")
    return results
