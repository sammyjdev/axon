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

import os
from uuid import UUID

import asyncpg
from pgvector.asyncpg import register_vector

from axon.embedder.lesson_embedding import SupportsEmbedOne
from axon.models.lesson import LessonRecord
from axon.store.vector_common import VECTOR_SIZE

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
    vector      vector({VECTOR_SIZE})
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
        finally:
            await con.close()
        return [self._row_to_lesson(row) for row in rows]

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
