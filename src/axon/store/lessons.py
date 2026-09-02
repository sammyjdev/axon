"""Postgres-backed store for the lessons corpus.

Two patterns are borrowed deliberately, from two different places:

* **A fresh connection per call**, like ``FailureStore``. This store is
  low-traffic and its callers are independent ``asyncio.run`` invocations; an
  asyncpg pool is bound to the loop that created it, so a pool could not be
  reused across them.
* **Extension bootstrap then dimension guard before any DDL**, like
  ``PgVectorStore._ensure_pool``. The table is created here rather than by a
  numbered migration: the migration chain belongs to the SESSION store and runs
  against databases where pgvector was never installed, so a vector column in it
  breaks every one of them (measured on the Wave A merge: 18 tests, ``type
  "vector" does not exist``).

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
from pgvector.asyncpg import register_vector

from axon.embedder.lesson_embedding import SupportsEmbedOne
from axon.models.lesson import LessonRecord
from axon.store.vector_common import VECTOR_SIZE

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
    vector      vector({VECTOR_SIZE}),
    -- Delivery, not capture. Without these, a lesson nobody has ever read and one read
    -- daily are the same row, so "does anything consume this?" has no answer and the
    -- assumed answer is the flattering one. Measured 2026-09-01: 24 lessons, zero way to
    -- tell. The corpus's own lesson about quiet mechanisms applies to the corpus.
    retrieved_count   integer NOT NULL DEFAULT 0,
    last_retrieved_at timestamptz
)
"""


class LessonStore:
    """Insert, fetch and search lessons by cosine distance."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def init(self) -> None:
        """Bootstrap the extension, refuse a mismatched table, then create it.

        The bootstrap runs on a plain connection because ``register_vector``
        introspects the type codec and cannot do so before the extension exists.
        """
        con = await asyncpg.connect(self._dsn)
        try:
            await con.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await self._check_dimension_guard(con)
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

    async def _check_dimension_guard(self, con: asyncpg.Connection) -> None:
        """Refuse an existing table whose vector dim is not the current one.

        pgvector stores the declared dimension in ``atttypmod`` as-is (no
        VARHDRSZ-style offset). An absent table is a no-op: the DDL below
        creates it at VECTOR_SIZE.
        """
        existing_dim = await con.fetchval(
            """
            SELECT atttypmod FROM pg_attribute
            WHERE attrelid = to_regclass($1) AND attname = 'vector' AND NOT attisdropped
            """,
            TABLE,
        )
        if existing_dim is not None and existing_dim != VECTOR_SIZE:
            raise ValueError(
                f"{TABLE}.vector is dim {existing_dim} but the embedder now produces "
                f"{VECTOR_SIZE}; mixed dims are not allowed."
            )

    async def insert(self, lesson: LessonRecord) -> UUID:
        """Store ``lesson`` and return its id."""
        con = await self._connect()
        try:
            await con.execute(
                "INSERT INTO lessons"
                " (id, kind, triggers, mistake, tell, fix, source, created_at, vector)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                lesson.id, lesson.kind, lesson.triggers, lesson.mistake, lesson.tell,
                lesson.fix, lesson.source, lesson.created_at, self._as_vector(lesson.embedding),
            )
        finally:
            await con.close()
        return lesson.id

    async def get(self, lesson_id: UUID) -> LessonRecord | None:
        """Return the lesson with ``lesson_id``, or None if there is none."""
        con = await self._connect()
        try:
            row = await con.fetchrow(
                "SELECT id, kind, triggers, mistake, tell, fix, source, created_at, vector"
                " FROM lessons WHERE id = $1",
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
                "SELECT id, kind, triggers, mistake, tell, fix, source, created_at, vector"
                " FROM lessons WHERE source = $1 LIMIT 1",
                source,
            )
        finally:
            await con.close()
        return self._row_to_lesson(row) if row is not None else None

    async def update(self, lesson: LessonRecord) -> None:
        """Overwrite the content and vector of the row at ``lesson.id``.

        ``id`` and ``created_at`` are not among the SET columns: an update
        corrects an existing entry, it does not mint a new one.
        """
        con = await self._connect()
        try:
            await con.execute(
                "UPDATE lessons SET kind = $2, triggers = $3, mistake = $4, tell = $5,"
                " fix = $6, source = $7, vector = $8 WHERE id = $1",
                lesson.id, lesson.kind, lesson.triggers, lesson.mistake, lesson.tell,
                lesson.fix, lesson.source, self._as_vector(lesson.embedding),
            )
        finally:
            await con.close()

    async def search(
        self,
        query: str,
        *,
        engine: SupportsEmbedOne,
        kind: str | None = None,
        triggers: list[str] | None = None,
        limit: int = 5,
    ) -> list[LessonRecord]:
        """Return up to ``limit`` lessons nearest ``query`` by cosine distance.

        ``query`` is embedded through ``engine`` - the same injected protocol
        Task 5 uses for lessons themselves - so storage and retrieval share one
        text-to-vector path rather than growing a second one here. A lesson
        with no vector can never be nearest to anything and is excluded.
        """
        vector = self._as_vector(engine.embed_one(query))
        con = await self._connect()
        try:
            params: list = [vector]
            clauses = ["vector IS NOT NULL"]
            if kind is not None:
                params.append(kind)
                clauses.append(f"kind = ${len(params)}")
            if triggers is not None:
                params.append(triggers)
                clauses.append(f"triggers && ${len(params)}::text[]")
            where = " AND ".join(clauses)
            select_cols = "id, kind, triggers, mistake, tell, fix, source, created_at, vector"
            sql = f"SELECT {select_cols} FROM {TABLE} WHERE {where} ORDER BY vector <=> $1 LIMIT {int(limit)}"  # noqa: S608, E501
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

        No `vector IS NOT NULL` filter either, unlike `search()`. That filter is there
        because an unembedded lesson can never be nearest to anything; a catalogue is not a
        ranking, and omitting a lesson that exists is the exact failure this list is for.

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
        con = await asyncpg.connect(self._dsn)
        await register_vector(con)
        return con

    @staticmethod
    def _as_vector(embedding: list[float] | None) -> list[float] | None:
        if embedding is None:
            return None
        if len(embedding) != VECTOR_SIZE:
            raise ValueError(
                f"embedding has {len(embedding)} dims but the table is {VECTOR_SIZE}"
            )
        return embedding

    @staticmethod
    def _row_to_lesson(row: asyncpg.Record) -> LessonRecord:
        # register_vector decodes the column into a pgvector Vector, not a list.
        vector = row["vector"]
        return LessonRecord(
            id=row["id"],
            kind=row["kind"],
            triggers=list(row["triggers"]),
            mistake=row["mistake"],
            tell=row["tell"],
            fix=row["fix"],
            source=row["source"],
            created_at=row["created_at"],
            embedding=None if vector is None else [float(v) for v in vector.to_list()],
        )

    async def close(self) -> None:
        """No-op: connections are opened and closed per call."""
