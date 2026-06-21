from __future__ import annotations

import asyncpg
from pgvector.asyncpg import register_vector

from axon.store.vector_store import VECTOR_SIZE


async def _init_conn(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


class PgVectorStore:
    """pgvector-backed implementation of the VectorStore surface (dec-121 step 1).

    One `embeddings` table; `ctx` is a filter column (not per-ctx tables).
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            # The vector extension must exist before register_vector can
            # introspect the type codec. Bootstrap it on a plain connection
            # first, then create the pool with the init hook.
            bootstrap = await asyncpg.connect(self._dsn)
            try:
                await bootstrap.execute("CREATE EXTENSION IF NOT EXISTS vector")
            finally:
                await bootstrap.close()
            self._pool = await asyncpg.create_pool(self._dsn, init=_init_conn, min_size=1, max_size=5)
        return self._pool

    async def ensure_collections(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
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
                    git_commit  text DEFAULT '',
                    modified_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            await con.execute(
                "CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw "
                "ON embeddings USING hnsw (vector vector_cosine_ops)"
            )
            await con.execute(
                "CREATE INDEX IF NOT EXISTS idx_embeddings_ctx_file ON embeddings (ctx, file_path)"
            )

    async def upsert(self, chunk) -> None:  # chunk: Chunk
        await self.upsert_batch([chunk])

    async def upsert_batch(self, chunks) -> None:  # chunks: list[Chunk]
        if not chunks:
            return
        pool = await self._ensure_pool()
        rows = [
            (
                c.id, c.vector, c.ctx, c.file_path, c.language, c.chunk_type,
                c.symbol, c.project, c.content, c.git_commit, c.modified_at,
            )
            for c in chunks
        ]
        async with pool.acquire() as con, con.transaction():
            await con.executemany(
                """
                INSERT INTO embeddings
                    (id, vector, ctx, file_path, language, chunk_type, symbol,
                     project, content, git_commit, modified_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                ON CONFLICT (id) DO UPDATE SET
                    vector=EXCLUDED.vector, ctx=EXCLUDED.ctx, file_path=EXCLUDED.file_path,
                    language=EXCLUDED.language, chunk_type=EXCLUDED.chunk_type,
                    symbol=EXCLUDED.symbol, project=EXCLUDED.project, content=EXCLUDED.content,
                    git_commit=EXCLUDED.git_commit, modified_at=EXCLUDED.modified_at
                """,
                rows,
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
