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

Idempotency: NOT enforced here. ``axon_record_lesson`` always mints a fresh
UUID and ``LessonStore.insert`` has no dedup-by-source check, and neither is
this module's place to add one - the DSN, the store and the tool boundary
belong to earlier tasks, and reaching into ``mcp/server.py`` here would
collide with Task 10, in flight on this same corpus concurrently. Re-running
``seed_corpus`` against a live database WILL duplicate every row. The
``source`` field is deliberately built as a stable, greppable key
(``file#id``) so a future dedup check - ``SELECT 1 FROM lessons WHERE
source = $1`` - has something to key on; that check does not exist yet.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

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


async def seed_corpus(
    path: Path,
    record: Callable[..., Awaitable[str]],
) -> list[str]:
    """Call ``record`` (``axon_record_lesson``) once per entry in the corpus at ``path``.

    Returns the tool's return values, one per entry, in file order.
    """
    return [
        await record(**corpus_entry_to_lesson_kwargs(entry, path))
        for entry in load_corpus(path)
    ]
