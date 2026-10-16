"""Postgres-backed store for the lessons corpus.

A fresh connection per call, like ``FailureStore``: this store is low-traffic and
its callers are independent ``asyncio.run`` invocations; an asyncpg pool is bound
to the loop that created it, so a pool could not be reused across them. The table
is created here rather than by the session store's numbered migrations.

Search is Postgres full-text search, so recording and finding a lesson needs no
embedding provider and no key. A table created before 2026-10-15 still carries a
``vector`` column; nothing reads or writes it any more.

The DSN is its own config, ``AXON_LESSONS_PG_URL`` - not ``rt.pg_url`` /
``AXON_PG_URL``, the DSN every other store here shares. Reusing the shared
one would put a client's lessons in whatever database every other store
already writes to, purely because nobody set a lessons-specific value; see
``resolve_lessons_dsn``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from axon.models.lesson import LessonRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LessonHandle:
    """What a lesson looks like from outside: enough to decide whether to ask for it.

    Not a `LessonRecord`. The mistake/tell/fix ARE the lesson, and the whole point of a
    catalogue is that they stay behind `search_lessons` - a list carrying them would cost
    every session the entire corpus in tokens and leave nothing to retrieve.
    """

    id: UUID
    kind: str
    triggers: list[str]

TABLE = "lessons"

_DSN_ENV_VAR = "AXON_LESSONS_PG_URL"


def resolve_lessons_dsn() -> str:
    """Resolve the lessons DSN from config, with no fallback.

    Isolation here is the connection, not a permission check: a missing
    ``AXON_LESSONS_PG_URL`` must refuse, not quietly reuse ``AXON_PG_URL``
    (the DSN every other store shares) or a hardcoded default - either would
    silently put a client's lessons in the same database as everything else.
    """
    dsn = os.environ.get(_DSN_ENV_VAR)
    if not dsn:
        raise RuntimeError(
            f"{_DSN_ENV_VAR} is not set. There is no fallback: set it to the "
            f"database that should hold this client's lessons."
        )
    return dsn

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id          uuid PRIMARY KEY,
    kind        text NOT NULL,
    triggers    text[] NOT NULL,
    mistake     text NOT NULL,
    tell        text NOT NULL,
    fix         text NOT NULL,
    source      text NOT NULL,
    created_at  timestamptz NOT NULL,
    -- Delivery, not capture. Without these, a lesson nobody has ever read and one read
    -- daily are the same row, so "does anything consume this?" has no answer and the
    -- assumed answer is the flattering one. Measured 2026-09-01: 24 lessons, zero way to
    -- tell. The corpus's own lesson about quiet mechanisms applies to the corpus.
    retrieved_count   integer NOT NULL DEFAULT 0,
    last_retrieved_at timestamptz
)
"""


_COLS = "id, kind, triggers, mistake, tell, fix, source, created_at"

_DOCUMENT = (
    "(setweight(to_tsvector('english', kind || ' ' || array_to_string(triggers, ' ')), 'A')"
    " || setweight(to_tsvector('english', mistake), 'B')"
    " || setweight(to_tsvector('english', tell), 'C')"
    " || setweight(to_tsvector('english', fix), 'D'))"
)

# The query's own lexemes, OR-ed. Built from to_tsvector rather than by editing the
# text of plainto_tsquery, so no character of the query can act as an operator.
_ANY_WORD = (
    "SELECT to_tsquery('simple', string_agg(quote_literal(word), ' | ')) AS query"
    " FROM unnest(tsvector_to_array(to_tsvector('english', $1))) AS word"
)


class LessonStore:
    """Insert, fetch and search lessons by full-text match."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def init(self) -> None:
        """Create the table and add the delivery counters if they are missing."""
        con = await asyncpg.connect(self._dsn)
        try:
            await con.execute(_DDL)
            # CREATE TABLE IF NOT EXISTS is a no-op on a table that already exists, so the
            # DDL above never reaches a deployment that predates these columns. Additive and
            # idempotent: the counters start at 0, which is the truth for every row written
            # before delivery was recorded.
            await con.execute(
                f"ALTER TABLE {TABLE} "
                "ADD COLUMN IF NOT EXISTS retrieved_count integer NOT NULL DEFAULT 0"
            )
            await con.execute(
                f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS last_retrieved_at timestamptz"
            )
        finally:
            await con.close()

    async def insert(self, lesson: LessonRecord) -> UUID:
        """Store ``lesson`` and return its id."""
        con = await self._connect()
        try:
            await con.execute(
                "INSERT INTO lessons"
                " (id, kind, triggers, mistake, tell, fix, source, created_at)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                lesson.id, lesson.kind, lesson.triggers, lesson.mistake, lesson.tell,
                lesson.fix, lesson.source, lesson.created_at,
            )
        finally:
            await con.close()
        return lesson.id

    async def get(self, lesson_id: UUID) -> LessonRecord | None:
        """Return the lesson with ``lesson_id``, or None if there is none."""
        con = await self._connect()
        try:
            row = await con.fetchrow(
                f"SELECT {_COLS} FROM lessons WHERE id = $1",  # noqa: S608
                lesson_id,
            )
        finally:
            await con.close()
        return self._row_to_lesson(row) if row is not None else None

    async def get_by_source(self, source: str) -> LessonRecord | None:
        """Return a lesson with this exact ``source``, or None if there is none.

        ``source`` is not unique in this table (see the module docstring on
        why no UNIQUE constraint exists), so this returns whichever match
        Postgres hands back first. Callers that mint their own stable,
        collision-free ``source`` keys - like the corpus seed - are the
        only ones for whom "a match" and "the match" coincide.
        """
        con = await self._connect()
        try:
            row = await con.fetchrow(
                f"SELECT {_COLS} FROM lessons WHERE source = $1 LIMIT 1",  # noqa: S608
                source,
            )
        finally:
            await con.close()
        return self._row_to_lesson(row) if row is not None else None

    async def update(self, lesson: LessonRecord) -> None:
        """Overwrite the content of the row at ``lesson.id``.

        ``id`` and ``created_at`` are not among the SET columns: an update
        corrects an existing entry, it does not mint a new one.
        """
        con = await self._connect()
        try:
            await con.execute(
                "UPDATE lessons SET kind = $2, triggers = $3, mistake = $4, tell = $5,"
                " fix = $6, source = $7 WHERE id = $1",
                lesson.id, lesson.kind, lesson.triggers, lesson.mistake, lesson.tell,
                lesson.fix, lesson.source,
            )
        finally:
            await con.close()

    async def search(
        self,
        query: str,
        *,
        kind: str | None = None,
        triggers: list[str] | None = None,
        limit: int = 5,
    ) -> list[LessonRecord]:
        """Return up to ``limit`` lessons that share a word with ``query``, best first.

        Any query word can match (an OR of its stemmed lexemes); rank is cover
        density over a document weighted triggers > mistake > tell > fix,
        normalised by document length. A query with no searchable word returns
        nothing.
        """
        con = await self._connect()
        try:
            params: list = [query]
            clauses = [f"{_DOCUMENT} @@ q.query"]
            if kind is not None:
                params.append(kind)
                clauses.append(f"kind = ${len(params)}")
            if triggers is not None:
                params.append(triggers)
                clauses.append(f"triggers && ${len(params)}::text[]")
            where = " AND ".join(clauses)
            # shortcut: the document is built per row on every search, fine for the
            # dozens of lessons there are; add a stored tsvector column with a GIN
            # index if the table reaches thousands of rows.
            sql = (
                f"SELECT {_COLS} FROM {TABLE}, ({_ANY_WORD}) q "  # noqa: S608
                f"WHERE {where} "
                f"ORDER BY ts_rank_cd({_DOCUMENT}, q.query, 1) DESC, created_at DESC "
                f"LIMIT {int(limit)}"
            )
            rows = await con.fetch(sql, *params)
            # Guarded HERE as well as inside: the rule is that delivery never depends on
            # measuring delivery, and a rule that only holds while the current
            # implementation is in place is not the rule.
            try:
                await self._record_retrieval(con, [row["id"] for row in rows])
            except Exception:  # noqa: BLE001 - never cost the caller its results
                # Logged, not swallowed. A bare `except: pass` is precisely the quiet
                # mechanism this bookkeeping exists to detect elsewhere; it must not be the
                # shape of the bookkeeping itself.
                logger.warning("lesson retrieval bookkeeping failed", exc_info=True)
        finally:
            await con.close()
        return [self._row_to_lesson(row) for row in rows]

    async def catalog(self) -> list[LessonHandle]:
        """Every lesson's id, kind and triggers - and NOT a delivery.

        Deliberately not `search(limit=n)` with a throwaway query. `search()` credits
        `retrieved_count` for the rows it returns, which is what turned "does anything read
        this?" into an answerable question; a catalogue rendered on every session start
        would credit five lessons per session and overwrite the 24-lessons/0-deliveries
        baseline with a number that measures the hook instead of any agent's decision to
        consult one. The counters must keep meaning what they mean.

        Newest first: a corpus grows, the render truncates, and the lesson written today is
        the one most likely to be about a mistake still being made.
        """
        con = await self._connect()
        try:
            rows = await con.fetch(
                f"SELECT id, kind, triggers FROM {TABLE} ORDER BY created_at DESC"  # noqa: S608
            )
        finally:
            await con.close()
        return [
            LessonHandle(id=r["id"], kind=r["kind"], triggers=list(r["triggers"]))
            for r in rows
        ]

    async def _record_retrieval(self, con: asyncpg.Connection, ids: list) -> None:
        """Credit exactly the lessons that came back, and never at the caller's expense.

        Only the returned ids: crediting every row on every search would make the whole
        corpus look used, which is the state this measurement exists to leave.

        Failures are swallowed. Delivery must not depend on measuring delivery - a search
        that returns nothing because a counter write failed is strictly worse than a search
        with no counters at all.
        """
        if not ids:
            return
        try:
            # S608: the only interpolation is TABLE, a module constant - the ids go
            # through a bound parameter, same as every other query here.
            await con.execute(
                f"UPDATE {TABLE} SET retrieved_count = retrieved_count + 1, "  # noqa: S608
                "last_retrieved_at = now() WHERE id = ANY($1::uuid[])",
                ids,
            )
        except Exception:  # noqa: BLE001 - see the docstring: never cost the caller
            logger.warning("could not record lesson retrieval", exc_info=True)

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(self._dsn)

    @staticmethod
    def _row_to_lesson(row: asyncpg.Record) -> LessonRecord:
        return LessonRecord(
            id=row["id"],
            kind=row["kind"],
            triggers=list(row["triggers"]),
            mistake=row["mistake"],
            tell=row["tell"],
            fix=row["fix"],
            source=row["source"],
            created_at=row["created_at"],
        )

    async def close(self) -> None:
        """No-op: connections are opened and closed per call."""
