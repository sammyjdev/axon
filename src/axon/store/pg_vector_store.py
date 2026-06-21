from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
from pgvector.asyncpg import register_vector

from axon.store.vector_store import VECTOR_SIZE, _rank_and_limit


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

    async def search(
        self,
        query_vector,
        collections,
        language=None,
        project=None,
        top_k: int = 5,
        max_depth: int = 1,
        max_nodes: int = 25,
        max_tokens: int = 1200,
    ) -> list[dict]:
        _ = max_depth  # accepted for parity, unused (matches the Qdrant backend)
        pool = await self._ensure_pool()
        clauses = ["ctx = ANY($2)"]
        params: list = [query_vector, list(collections)]
        if language:
            params.append(language)
            clauses.append(f"language = ${len(params)}")
        if project:
            params.append(project)
            clauses.append(f"project = ${len(params)}")
        where = " AND ".join(clauses)
        sql = f"""
            SELECT id, file_path, language, chunk_type, symbol, project, content,
                   git_commit, modified_at, 1 - (vector <=> $1) AS score
            FROM embeddings
            WHERE {where}
            ORDER BY vector <=> $1
            LIMIT {int(top_k)}
        """
        async with pool.acquire() as con:
            records = await con.fetch(sql, *params)
        results = [
            {
                "score": float(r["score"]),
                "id": r["id"],
                "payload": {
                    "file_path": r["file_path"], "language": r["language"],
                    "chunk_type": r["chunk_type"], "symbol": r["symbol"],
                    "project": r["project"], "content": r["content"],
                    "git_commit": r["git_commit"],
                    "modified_at": r["modified_at"].isoformat(),
                },
            }
            for r in records
        ]
        return _rank_and_limit(
            results, top_k=top_k, max_nodes=max_nodes, max_tokens=max_tokens,
            now=datetime.now(UTC),
        )

    async def delete_by_file(self, ctx: str, file_path: str) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute("DELETE FROM embeddings WHERE ctx=$1 AND file_path=$2", ctx, file_path)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
