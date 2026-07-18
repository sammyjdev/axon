from __future__ import annotations

import os
import re
from datetime import UTC, datetime

import asyncpg
from pgvector.asyncpg import register_vector

from axon.store.vector_common import VECTOR_SIZE, _rank_and_limit

_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _append_filter_clauses(params: list, *, language: str | None, project: str | None) -> str:
    """Shared WHERE builder for the dense and lexical arms.

    Both arms MUST filter identically (ctx isolation is a security boundary);
    a single builder makes divergence impossible.
    """
    clauses = ["ctx = ANY($2)"]
    if language:
        params.append(language)
        clauses.append(f"language = ${len(params)}")
    if project:
        params.append(project)
        clauses.append(f"project = ${len(params)}")
    return " AND ".join(clauses)


_RRF_K = 60  # Standard RRF dampening constant; keeps both rank arms comparable.


async def _init_conn(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


class PgVectorStore:
    """pgvector-backed implementation of the VectorStore surface (dec-121 step 1).

    One table (default ``embeddings``); ``ctx`` is a filter column (not per-ctx tables).
    The table name is parameterised so the recall harness can target an isolated
    ``recall_embeddings`` table without touching production data.
    """

    def __init__(self, dsn: str, table: str = "embeddings") -> None:
        if not _TABLE_RE.fullmatch(table):
            raise ValueError(f"invalid table name {table!r}: must match ^[a-z_][a-z0-9_]*$")
        self._dsn = dsn
        self._table = table
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            # The vector extension must exist before register_vector can
            # introspect the type codec. Bootstrap it on a plain connection
            # first, then create the pool with the init hook.
            bootstrap = await asyncpg.connect(self._dsn)
            try:
                await bootstrap.execute("CREATE EXTENSION IF NOT EXISTS vector")
                await self._check_dimension_guard(bootstrap)
            finally:
                await bootstrap.close()
            self._pool = await asyncpg.create_pool(
                self._dsn, init=_init_conn, min_size=1, max_size=5
            )
        return self._pool

    async def _check_dimension_guard(self, con: asyncpg.Connection) -> None:
        """Refuse to touch an existing table whose ``vector`` column dim
        doesn't match the current VECTOR_SIZE. Mixed dims are not allowed
        (EMB-3): pgvector stores the declared dimension directly in
        ``atttypmod`` (no VARHDRSZ-style offset), so it's read as-is.

        A fresh/absent table is a no-op here -- ``ensure_collections`` will
        create it at VECTOR_SIZE.
        """
        existing_dim = await con.fetchval(
            """
            SELECT atttypmod FROM pg_attribute
            WHERE attrelid = to_regclass($1) AND attname = 'vector' AND NOT attisdropped
            """,
            self._table,
        )
        if existing_dim is not None and existing_dim != VECTOR_SIZE:
            raise ValueError(
                f"{self._table}.vector is dim {existing_dim} but the embedder now "
                f"produces {VECTOR_SIZE}; run the bge-m3 re-index (EMB-5) -- mixed "
                f"dims are not allowed."
            )

    async def ensure_collections(self) -> None:
        pool = await self._ensure_pool()
        t = self._table
        async with pool.acquire() as con:
            await con.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await con.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {t} (
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
            # First run against an existing populated table takes an ACCESS
            # EXCLUSIVE lock and rewrites every row (generated STORED column).
            # Run ensure_collections out-of-band after deploy on large tables
            # rather than letting a latency-sensitive path trigger it.
            await con.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS content_tsv "
                "tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED"
            )
            await con.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_hnsw "
                f"ON {t} USING hnsw (vector vector_cosine_ops)"
            )
            # A rename-based migration can leave idx_{t}_hnsw attached to a
            # previous table; IF NOT EXISTS then no-ops on the NAME and this
            # table silently runs unindexed (observed after the bge-m3
            # re-index). Verify presence ON THE TABLE and heal with a
            # deterministic fallback name.
            hnsw_rows = await con.fetch(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = $1 AND indexdef ILIKE '%hnsw%'",
                t,
            )
            if not hnsw_rows:
                await con.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{t}_hnsw2 "
                    f"ON {t} USING hnsw (vector vector_cosine_ops)"
                )
            await con.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_ctx_file ON {t} (ctx, file_path)"
            )
            await con.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_content_tsv ON {t} USING GIN (content_tsv)"
            )

    async def upsert(self, chunk) -> None:  # chunk: Chunk
        await self.upsert_batch([chunk])

    async def upsert_batch(self, chunks) -> None:  # chunks: list[Chunk]
        if not chunks:
            return
        pool = await self._ensure_pool()
        rows = [
            (
                c.id,
                c.vector,
                c.ctx,
                c.file_path,
                c.language,
                c.chunk_type,
                c.symbol,
                c.project,
                c.content,
                c.git_commit,
                c.modified_at,
            )
            for c in chunks
        ]
        t = self._table
        async with pool.acquire() as con, con.transaction():
            await con.executemany(
                f"""
                INSERT INTO {t}
                    (id, vector, ctx, file_path, language, chunk_type, symbol,
                     project, content, git_commit, modified_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                ON CONFLICT (id) DO UPDATE SET
                    vector=EXCLUDED.vector, ctx=EXCLUDED.ctx, file_path=EXCLUDED.file_path,
                    language=EXCLUDED.language, chunk_type=EXCLUDED.chunk_type,
                    symbol=EXCLUDED.symbol, project=EXCLUDED.project, content=EXCLUDED.content,
                    git_commit=EXCLUDED.git_commit, modified_at=EXCLUDED.modified_at
                """,  # noqa: S608
                rows,
            )

    async def search(
        self,
        query_vector,
        collections,
        query: str | None = None,
        language=None,
        project=None,
        top_k: int = 5,
        max_depth: int = 1,
        max_nodes: int = 25,
        max_tokens: int = 1200,
    ) -> list[dict]:
        _ = max_depth  # accepted for parity, unused (matches the vector backend interface)
        pool = await self._ensure_pool()
        params: list = [query_vector, list(collections)]
        where = _append_filter_clauses(params, language=language, project=project)
        sql = f"""
            SELECT id, file_path, language, chunk_type, symbol, project, content,
                   git_commit, modified_at, 1 - (vector <=> $1) AS score
            FROM {self._table}
            WHERE {where}
            ORDER BY vector <=> $1
            LIMIT {int(top_k)}
        """  # noqa: S608
        async with pool.acquire() as con:
            records = await con.fetch(sql, *params)
            lexical_records = []
            if os.environ.get("AXON_HYBRID_SEARCH") == "1" and query:
                lex_params: list = [query, list(collections)]
                lex_where = _append_filter_clauses(lex_params, language=language, project=project)
                lex_sql = f"""
                    SELECT id, file_path, language, chunk_type, symbol, project, content,
                           git_commit, modified_at, ts_rank(content_tsv, q.query) AS score
                    FROM {self._table}, websearch_to_tsquery('simple', $1) AS q(query)
                    WHERE content_tsv @@ q.query AND {lex_where}
                    ORDER BY ts_rank(content_tsv, q.query) DESC
                    LIMIT {int(top_k)}
                """  # noqa: S608
                lexical_records = await con.fetch(lex_sql, *lex_params)
        results = [_record_to_result(r) for r in records]
        if lexical_records:
            results = _merge_rrf_arms(results, [_record_to_result(r) for r in lexical_records])
        return _rank_and_limit(
            results,
            top_k=top_k,
            max_nodes=max_nodes,
            max_tokens=max_tokens,
            now=datetime.now(UTC),
        )

    async def delete_by_file(self, ctx: str, file_path: str) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                f"DELETE FROM {self._table} WHERE ctx=$1 AND file_path=$2",  # noqa: S608
                ctx,
                file_path,
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def _record_to_result(r) -> dict:
    return {
        "score": float(r["score"]),
        "id": r["id"],
        "payload": {
            "file_path": r["file_path"],
            "language": r["language"],
            "chunk_type": r["chunk_type"],
            "symbol": r["symbol"],
            "project": r["project"],
            "content": r["content"],
            "git_commit": r["git_commit"],
            "modified_at": r["modified_at"].isoformat(),
        },
    }


def _merge_rrf_arms(dense: list[dict], lexical: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    scores: dict[str, float] = {}

    for rank, item in enumerate(dense, start=1):
        item_id = str(item["id"])
        payload = dict(item.get("payload") or {})
        payload["dense_score"] = float(item["score"])
        merged[item_id] = {**item, "payload": payload}
        scores[item_id] = scores.get(item_id, 0.0) + (1 / (_RRF_K + rank))

    for rank, item in enumerate(lexical, start=1):
        item_id = str(item["id"])
        merged.setdefault(item_id, {**item, "payload": dict(item.get("payload") or {})})
        scores[item_id] = scores.get(item_id, 0.0) + (1 / (_RRF_K + rank))

    results = [{**item, "score": scores[item_id]} for item_id, item in merged.items()]
    results.sort(key=lambda item: (-float(item["score"]), str(item.get("id", ""))))
    return results
