"""Schema parity tests for identity and kind (Task 2 / ID-7, ID-8, ID-9).

Verifies that:
- Fresh database and migrated database converge to identical columns, constraints, and indexes.
- Migration applied before tables exist converges once in-code DDL runs.
- Running migrations and in-code DDL twice is a no-op.
- Pre-migration rows survive with kind NULL and file_index.repo empty.
- file_index primary key moves to (repo, file_path, ctx).
- Two repos can hold the same relative path in the same ctx.
- PostgresFileCache.set_entry writes and updates in place after the PK move.
"""
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("testcontainers.postgres")
import asyncpg  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.pg_file_cache import PostgresFileCache  # noqa: E402
from axon.store.pg_migrations import (  # noqa: E402
    PG_MIGRATIONS_DIR,
    apply_pg_migrations,
)
from axon.store.pg_vector_store import (  # noqa: E402
    VECTOR_SIZE,
    PgVectorStore,
)


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16",
        username="axon",
        password="axon",  # noqa: S106
        dbname="axon",
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def _get_columns(
    con: asyncpg.Connection, table: str
) -> dict[str, tuple[str, str, str | None]]:
    rows = await con.fetch(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        """,
        table,
    )
    return {
        r["column_name"]: (r["data_type"], r["is_nullable"], r["column_default"])
        for r in rows
    }


async def _get_constraints(con: asyncpg.Connection, table: str) -> dict[str, list[str]]:
    rows = await con.fetch(
        """
        SELECT
            c.conname,
            COALESCE(
                array_agg(a.attname ORDER BY u.ord),
                ARRAY[]::text[]
            ) AS cols
        FROM pg_constraint c
        LEFT JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
        LEFT JOIN pg_attribute a
            ON a.attrelid = c.conrelid AND a.attnum = u.attnum
        WHERE c.conrelid = $1::regclass
        GROUP BY c.conname
        """,
        table,
    )
    return {r["conname"]: [col for col in r["cols"] if col is not None] for r in rows}


async def _get_indexes(con: asyncpg.Connection, table: str) -> dict[str, str]:
    rows = await con.fetch(
        """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = $1
        """,
        table,
    )
    return {r["indexname"]: r["indexdef"] for r in rows}


async def _snapshot(con: asyncpg.Connection) -> dict[str, Any]:
    tables = ["embeddings", "file_index"]
    snap = {}
    for t in tables:
        snap[f"{t}.columns"] = await _get_columns(con, t)
        snap[f"{t}.constraints"] = await _get_constraints(con, t)
        snap[f"{t}.indexes"] = await _get_indexes(con, t)
    return snap


async def _clean_schema(con: asyncpg.Connection) -> None:
    await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")


async def _build_fresh(dsn: str, con: asyncpg.Connection) -> None:
    store = PgVectorStore(dsn)
    try:
        await store.ensure_collections()
    finally:
        await store.close()
    cache = PostgresFileCache(dsn)
    try:
        await cache.ensure_schema()
    finally:
        await cache.close()
    await apply_pg_migrations(con, PG_MIGRATIONS_DIR)


async def _build_legacy(con: asyncpg.Connection) -> None:
    await con.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS embeddings (
            id          text PRIMARY KEY,
            vector      vector({VECTOR_SIZE}) NOT NULL,
            ctx         text NOT NULL,
            file_path   text NOT NULL,
            language    text,
            chunk_type  text,
            symbol      text,
            project     text,
            content     text,
            content_tsv tsvector GENERATED ALWAYS AS (
                to_tsvector('simple', content)
            ) STORED,
            git_commit  text DEFAULT '',
            modified_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    await con.execute(
        "ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS content_tsv "
        "tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED"
    )
    await con.execute(
        "CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw "
        "ON embeddings USING hnsw (vector vector_cosine_ops)"
    )
    await con.execute(
        "CREATE INDEX IF NOT EXISTS idx_embeddings_ctx_file ON embeddings (ctx, file_path)"
    )
    await con.execute(
        "CREATE INDEX IF NOT EXISTS idx_embeddings_content_tsv "
        "ON embeddings USING GIN (content_tsv)"
    )
    await con.execute(
        """
        CREATE TABLE IF NOT EXISTS file_index (
            file_path   text    NOT NULL,
            ctx         text    NOT NULL,
            sha1        text    NOT NULL,
            status      text    NOT NULL DEFAULT 'done',
            chunk_count integer NOT NULL DEFAULT 0,
            indexed_at  timestamptz NOT NULL,
            chunker_version text,
            PRIMARY KEY (file_path, ctx)
        )
        """
    )
    await con.execute(
        "ALTER TABLE file_index ADD COLUMN IF NOT EXISTS chunker_version text"
    )
    await con.execute(
        "CREATE INDEX IF NOT EXISTS ix_file_index_ctx ON file_index (ctx)"
    )
    await con.execute(
        "CREATE INDEX IF NOT EXISTS ix_file_index_status ON file_index (status)"
    )


async def _migrate_legacy(dsn: str, con: asyncpg.Connection) -> None:
    await _build_legacy(con)
    await apply_pg_migrations(con, PG_MIGRATIONS_DIR)
    store = PgVectorStore(dsn)
    try:
        await store.ensure_collections()
    finally:
        await store.close()
    cache = PostgresFileCache(dsn)
    try:
        await cache.ensure_schema()
    finally:
        await cache.close()


def _dummy_vector() -> str:
    return f"[{','.join(['0.1'] * VECTOR_SIZE)}]"


async def test_fresh_and_migrated_schemas_are_equal(pg_dsn: str) -> None:
    """ID-7, ID-8: fresh DDL and migrated legacy DDL converge to identical schema."""
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as con:
            await _clean_schema(con)
            await _build_fresh(pg_dsn, con)
            snap_fresh = await _snapshot(con)

            await _clean_schema(con)
            await _migrate_legacy(pg_dsn, con)
            snap_migrated = await _snapshot(con)

            assert "kind" in snap_fresh["embeddings.columns"]
            assert "repo" in snap_fresh["file_index.columns"]
            assert snap_fresh == snap_migrated
    finally:
        await pool.close()


async def test_the_migration_applied_before_the_tables_exist_still_converges(
    pg_dsn: str,
) -> None:
    """ID-8: running migration before in-code DDL creates tables still converges."""
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as con:
            await _clean_schema(con)
            await _build_fresh(pg_dsn, con)
            snap_fresh = await _snapshot(con)

            await _clean_schema(con)
            await apply_pg_migrations(con, PG_MIGRATIONS_DIR)
            store = PgVectorStore(pg_dsn)
            try:
                await store.ensure_collections()
            finally:
                await store.close()
            cache = PostgresFileCache(pg_dsn)
            try:
                await cache.ensure_schema()
            finally:
                await cache.close()

            snap_migrated_first = await _snapshot(con)
            assert snap_fresh == snap_migrated_first

            applied_rows = await con.fetch("SELECT version FROM schema_version")
            applied = {r["version"] for r in applied_rows}
            assert "0007_identity_and_kind" in applied
    finally:
        await pool.close()


async def test_running_everything_twice_is_a_no_op(pg_dsn: str) -> None:
    """ID-7 idempotence: repeated migration and in-code DDL leaves schema unchanged."""
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as con:
            await _clean_schema(con)
            await _migrate_legacy(pg_dsn, con)
            snap1 = await _snapshot(con)

            # Second execution
            await apply_pg_migrations(con, PG_MIGRATIONS_DIR)
            store = PgVectorStore(pg_dsn)
            try:
                await store.ensure_collections()
            finally:
                await store.close()
            cache = PostgresFileCache(pg_dsn)
            try:
                await cache.ensure_schema()
            finally:
                await cache.close()
            snap2 = await _snapshot(con)

            assert snap1 == snap2

            v7_count = await con.fetchval(
                "SELECT count(*) FROM schema_version WHERE version = '0007_identity_and_kind'"
            )
            assert v7_count == 1
    finally:
        await pool.close()


async def test_rows_written_before_the_migration_survive_it_unchanged(
    pg_dsn: str,
) -> None:
    """ID-7, ID-9: pre-migration rows survive with kind NULL and repo empty."""
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as con:
            await _clean_schema(con)
            await _build_legacy(con)

            dummy_vec = _dummy_vector()
            await con.execute(
                """
                INSERT INTO embeddings (
                    id, vector, ctx, file_path, language, chunk_type, symbol, project, content
                )
                VALUES (
                    'legacy-1', $1::vector, 'knowledge',
                    '/Users/test/vault/Decisions/dec-1.md',
                    'markdown', 'section', 'decision', 'Decisions',
                    'content of dec-1'
                )
                """,
                dummy_vec,
            )
            await con.execute(
                """
                INSERT INTO file_index (
                    file_path, ctx, sha1, status, chunk_count, indexed_at
                )
                VALUES (
                    '/Users/test/vault/Decisions/dec-1.md', 'knowledge',
                    'sha1-legacy', 'done', 1, now()
                )
                """
            )

            # Run migrations and in-code DDL
            await apply_pg_migrations(con, PG_MIGRATIONS_DIR)
            store = PgVectorStore(pg_dsn)
            try:
                await store.ensure_collections()
            finally:
                await store.close()
            cache = PostgresFileCache(pg_dsn)
            try:
                await cache.ensure_schema()
            finally:
                await cache.close()

            emb_row = await con.fetchrow(
                "SELECT id, file_path, project, kind FROM embeddings WHERE id = 'legacy-1'"
            )
            assert emb_row is not None
            assert emb_row["id"] == "legacy-1"
            assert emb_row["file_path"] == "/Users/test/vault/Decisions/dec-1.md"
            assert emb_row["project"] == "Decisions"
            assert emb_row["kind"] is None

            emb_count = await con.fetchval("SELECT count(*) FROM embeddings")
            assert emb_count == 1

            fi_row = await con.fetchrow(
                "SELECT file_path, ctx, sha1, repo FROM file_index WHERE file_path = $1",
                "/Users/test/vault/Decisions/dec-1.md",
            )
            assert fi_row is not None
            assert fi_row["file_path"] == "/Users/test/vault/Decisions/dec-1.md"
            assert fi_row["ctx"] == "knowledge"
            assert fi_row["sha1"] == "sha1-legacy"
            assert fi_row["repo"] == ""

            fi_count = await con.fetchval("SELECT count(*) FROM file_index")
            assert fi_count == 1
    finally:
        await pool.close()


async def test_the_primary_key_moved_to_repo_file_path_ctx(pg_dsn: str) -> None:
    """ID-7: the file_index primary key constraint is named file_index_repo_pkey."""
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as con:
            await _clean_schema(con)
            await _migrate_legacy(pg_dsn, con)

            pks = await con.fetch(
                """
                SELECT
                    c.conname,
                    array_agg(a.attname ORDER BY u.ord) AS cols
                FROM pg_constraint c
                CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS u(attnum, ord)
                JOIN pg_attribute a
                    ON a.attrelid = c.conrelid AND a.attnum = u.attnum
                WHERE c.conrelid = 'file_index'::regclass AND c.contype = 'p'
                GROUP BY c.conname
                """
            )
            assert len(pks) == 1
            assert pks[0]["conname"] == "file_index_repo_pkey"
            assert list(pks[0]["cols"]) == ["repo", "file_path", "ctx"]
    finally:
        await pool.close()


async def test_two_repos_can_hold_the_same_relative_path_in_one_ctx(
    pg_dsn: str,
) -> None:
    """ID-7: ('axon', path, ctx) and ('lume', path, ctx) can coexist; collision raises."""
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as con:
            await _clean_schema(con)
            await _migrate_legacy(pg_dsn, con)

            await con.execute(
                """
                INSERT INTO file_index (repo, file_path, ctx, sha1, indexed_at)
                VALUES ('axon', 'README.md', 'knowledge', 'sha-axon', now())
                """
            )
            await con.execute(
                """
                INSERT INTO file_index (repo, file_path, ctx, sha1, indexed_at)
                VALUES ('lume', 'README.md', 'knowledge', 'sha-lume', now())
                """
            )

            with pytest.raises(asyncpg.UniqueViolationError):
                await con.execute(
                    """
                    INSERT INTO file_index (repo, file_path, ctx, sha1, indexed_at)
                    VALUES ('axon', 'README.md', 'knowledge', 'sha-axon-dup', now())
                    """
                )
    finally:
        await pool.close()


async def test_set_entry_still_writes_after_the_key_moved(pg_dsn: str) -> None:
    """Seam check: PostgresFileCache.set_entry writes and updates in place."""
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as con:
            await _clean_schema(con)
            await _migrate_legacy(pg_dsn, con)
    finally:
        await pool.close()

    cache = PostgresFileCache(pg_dsn)
    try:
        await cache.set_entry("a/b.py", "knowledge", "s", 1)
        await cache.set_entry("a/b.py", "knowledge", "s", 1)
        entries = await cache.list_entries("knowledge")
        assert entries == [("a/b.py", "s")]
    finally:
        await cache.close()
