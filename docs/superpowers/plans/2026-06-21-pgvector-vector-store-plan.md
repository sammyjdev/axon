# PgVectorStore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `pgvector`-backed implementation of the existing `VectorStore` interface, selectable by env var, running in parallel to Qdrant, validated by the recall guard with no regression.

**Architecture:** A new `PgVectorStore` mirrors the `VectorStore` async surface 1:1 over a single Postgres `embeddings` table (one row per chunk, HNSW cosine index, `ctx` filter column). A `make_vector_store()` factory selects Qdrant or pgvector by `AXON_VECTOR_BACKEND`. The post-query staleness ranking + token-budget limiting is extracted into a shared helper so both backends rank identically. Qdrant stays the default; nothing else in dec-121 is touched.

**Tech Stack:** Python 3.11+, `asyncpg` + `pgvector` (asyncpg adapter), PostgreSQL with the `vector` extension, `testcontainers[postgres]` (image `pgvector/pgvector:pg16`), pytest + pytest-asyncio.

## Global Constraints

- Interface parity is the rule: `PgVectorStore` exposes the exact method names and signatures of `VectorStore` (`ensure_collections`, `upsert`, `upsert_batch`, `search(query_vector, collections, language=None, project=None, top_k=5, max_depth=1, max_nodes=25, max_tokens=1200) -> list[dict]`, `delete_by_file(ctx, file_path)`, `close`). Callers must not change beyond going through the factory.
- `max_depth` is accepted but unused (it is `_ = max_depth` in the Qdrant store today); preserve that - do NOT implement graph expansion.
- Search result shape is `list[dict]`, each `{"score": float, "payload": dict, "id": str}`, payload carrying `file_path, language, chunk_type, symbol, project, content, git_commit, modified_at` (`modified_at` as isoformat string, required by staleness ranking).
- Vector dimension is `VECTOR_SIZE` (`src/axon/store/vector_store.py:21`, `AXON_VECTOR_SIZE` or `default_embedding_dimension()`), NOT a hardcoded 768. The schema uses `vector(VECTOR_SIZE)`.
- Qdrant remains the default backend: with `AXON_VECTOR_BACKEND` unset, behavior is byte-for-byte unchanged.
- Only plain hyphens `-` in code/comments/docs, never em or en dashes.
- `id` is a uuid5 string (D1 stable chunk-id); store it as `text PRIMARY KEY` (opaque, no uuid casting). Upsert is `ON CONFLICT (id) DO UPDATE` so re-index never duplicates.
- NEVER load the ONNX model or hit a live Qdrant in unit tests. The recall gate (Task 9) is the only step that loads the model + needs GPU, and it is run on demand.

---

### Task 1: Dependencies, docker-compose service, and runtime config

**Files:**
- Modify: `pyproject.toml` (add `asyncpg`, `pgvector`, extend testcontainers extras)
- Modify: `docker-compose.yml` (add a `postgres` service)
- Modify: `src/axon/config/runtime.py` (`RuntimeConfig` gains `pg_url`)
- Test: `tests/config/test_runtime_pg.py`

**Interfaces:**
- Consumes: `RuntimeConfig` (existing dataclass/model), `load_runtime_config()`.
- Produces: `RuntimeConfig.pg_url: str` read from `AXON_PG_URL` (default `postgresql://axon:axon@localhost:5433/axon`).

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_runtime_pg.py
from __future__ import annotations


def test_pg_url_defaults(monkeypatch) -> None:
    monkeypatch.delenv("AXON_PG_URL", raising=False)
    from axon.config.runtime import load_runtime_config

    cfg = load_runtime_config()
    assert cfg.pg_url == "postgresql://axon:axon@localhost:5433/axon"


def test_pg_url_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AXON_PG_URL", "postgresql://u:p@host:5432/db")
    from axon.config.runtime import load_runtime_config

    cfg = load_runtime_config()
    assert cfg.pg_url == "postgresql://u:p@host:5432/db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_runtime_pg.py -v`
Expected: FAIL (`RuntimeConfig` has no attribute `pg_url`).

- [ ] **Step 3: Add `pg_url` to `RuntimeConfig` and `load_runtime_config`**

In `src/axon/config/runtime.py`, add a `pg_url` field to `RuntimeConfig` (next to `qdrant_url`) and populate it in `load_runtime_config` from the env:

```python
# in the RuntimeConfig definition, alongside qdrant_url:
pg_url: str = "postgresql://axon:axon@localhost:5433/axon"

# in load_runtime_config(), where qdrant_url is read:
pg_url=os.environ.get("AXON_PG_URL", "postgresql://axon:axon@localhost:5433/axon"),
```

(Match the existing construction style of `RuntimeConfig` in that function exactly - it is a dataclass/Pydantic model; add the field where the others are set.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_runtime_pg.py -v`
Expected: 2 passed.

- [ ] **Step 5: Add deps to `pyproject.toml`**

In `[project] dependencies`, add:
```
    "asyncpg>=0.29",
    "pgvector>=0.3",
```
Change the existing dev/test extra:
```
    "testcontainers[qdrant,redis]>=4.0.0",
```
to:
```
    "testcontainers[qdrant,redis,postgres]>=4.0.0",
```
Then install: `.venv/Scripts/python.exe -m pip install -e ".[dev]"` (use the project's actual extra name).

- [ ] **Step 6: Add an `axon-postgres` service to `docker-compose.yml`**

NOTE: `docker-compose.yml` ALREADY has a `postgres: image: postgres:16-alpine`
service (the langfuse backend). Do NOT reuse or modify it. Add a SEPARATE
service named `axon-postgres` with the pgvector image, on host port 5433 (the
AXON_PG_URL default), so the two never collide:

```yaml
  axon-postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: axon
      POSTGRES_PASSWORD: axon
      POSTGRES_DB: axon
    ports:
      - "5433:5432"
    volumes:
      - ./data/axon-postgres:/var/lib/postgresql/data
```

(Match the existing services' style - the qdrant/redis services use bind mounts
under `./data/`, so use the same rather than a named volume.)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml docker-compose.yml src/axon/config/runtime.py tests/config/test_runtime_pg.py
git commit -m "feat(pgvector): add asyncpg/pgvector deps, postgres compose service, AXON_PG_URL config"
```

---

### Task 2: Extract shared post-query ranking + limiting (DRY for parity)

**Files:**
- Modify: `src/axon/store/vector_store.py` (extract `_rank_and_limit`, call it from `search`)
- Test: `tests/store/test_rank_and_limit.py`

**Interfaces:**
- Consumes: `_apply_staleness_ranking(results, *, now)` (existing module function), `_utcnow()`.
- Produces: `_rank_and_limit(results: list[dict], *, top_k: int, max_nodes: int, max_tokens: int, now: datetime) -> list[dict]` - applies staleness ranking, then the token-budget + max_nodes limit, returns `limited[:top_k]`. `PgVectorStore.search` (Task 5) reuses it so both backends rank identically.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_rank_and_limit.py
from __future__ import annotations

from datetime import UTC, datetime


def _r(i: int, content: str) -> dict:
    return {"score": 1.0 - i * 0.01, "id": str(i),
            "payload": {"content": content, "modified_at": datetime(2025, 1, 1, tzinfo=UTC).isoformat()}}


def test_rank_and_limit_respects_top_k() -> None:
    from axon.store.vector_store import _rank_and_limit
    results = [_r(i, "word " * 10) for i in range(10)]
    out = _rank_and_limit(results, top_k=3, max_nodes=25, max_tokens=10_000, now=datetime(2025, 1, 2, tzinfo=UTC))
    assert len(out) == 3


def test_rank_and_limit_respects_token_budget() -> None:
    from axon.store.vector_store import _rank_and_limit
    # each content ~ 400 chars -> ~100 estimated tokens; budget 150 fits 1
    results = [_r(i, "x" * 400) for i in range(5)]
    out = _rank_and_limit(results, top_k=5, max_nodes=25, max_tokens=150, now=datetime(2025, 1, 2, tzinfo=UTC))
    assert len(out) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_rank_and_limit.py -v`
Expected: FAIL (`_rank_and_limit` not defined).

- [ ] **Step 3: Extract `_rank_and_limit` and call it from `search`**

Add this module-level function to `src/axon/store/vector_store.py` (after `_apply_staleness_ranking`):

```python
def _rank_and_limit(
    results: list[dict],
    *,
    top_k: int,
    max_nodes: int,
    max_tokens: int,
    now: datetime,
) -> list[dict]:
    """Staleness-rank then apply the max_nodes / token-budget cap. Shared by the
    Qdrant and pgvector backends so they rank identically."""
    ranked = _apply_staleness_ranking(results, now=now)
    limited: list[dict] = []
    token_budget = max_tokens
    for item in ranked:
        payload = item.get("payload") or {}
        content = str(payload.get("content", ""))
        estimated = max(1, len(content) // 4)
        if len(limited) >= max_nodes:
            break
        if token_budget - estimated < 0:
            break
        token_budget -= estimated
        limited.append(item)
    return limited[:top_k]
```

Then in `VectorStore.search` replace the inline `_apply_staleness_ranking(...)` + limiting loop (lines ~147-161) with:

```python
        return _rank_and_limit(
            results, top_k=top_k, max_nodes=max_nodes, max_tokens=max_tokens, now=_utcnow()
        )
```

- [ ] **Step 4: Run tests to verify pass + no regression**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_rank_and_limit.py tests/store/ -v -k "rank or vector_store" 2>&1 | tail -20`
Expected: new tests pass; any existing vector_store unit tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/axon/store/vector_store.py tests/store/test_rank_and_limit.py
git commit -m "refactor(vector): extract shared _rank_and_limit (staleness + budget) for backend parity"
```

---

### Task 3: PgVectorStore skeleton + ensure_collections (schema)

**Files:**
- Create: `src/axon/store/pg_vector_store.py`
- Test: `tests/store/test_pg_vector_store.py`

**Interfaces:**
- Consumes: `axon.store.vector_store.Chunk`, `VECTOR_SIZE`; `asyncpg`; `pgvector.asyncpg.register_vector`.
- Produces: `PgVectorStore(dsn: str)`, `async ensure_collections()`, `async close()`; a connection pool with the `vector` extension registered. Table `embeddings` with `vector(VECTOR_SIZE)`, HNSW cosine index, `(ctx, file_path)` index.

- [ ] **Step 1: Write the failing test (testcontainers[postgres])**

```python
# tests/store/test_pg_vector_store.py
from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer("pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon") as pg:
        # asyncpg DSN form
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_ensure_collections_idempotent(pg_dsn) -> None:
    from axon.store.pg_vector_store import PgVectorStore

    store = PgVectorStore(dsn=pg_dsn)
    try:
        await store.ensure_collections()
        await store.ensure_collections()  # second run must be a no-op
        # extension + table exist
        async with store._pool.acquire() as con:
            ext = await con.fetchval("SELECT 1 FROM pg_extension WHERE extname='vector'")
            tbl = await con.fetchval("SELECT to_regclass('public.embeddings')")
        assert ext == 1
        assert tbl is not None
    finally:
        await store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_pg_vector_store.py::test_ensure_collections_idempotent -v`
Expected: FAIL (module `pg_vector_store` does not exist).

- [ ] **Step 3: Write `PgVectorStore` skeleton + `ensure_collections`**

```python
# src/axon/store/pg_vector_store.py
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

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
```

Note: `register_vector` must run on every pooled connection (hence `init=_init_conn`), so `vector` params bind correctly in Tasks 4-5.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_pg_vector_store.py::test_ensure_collections_idempotent -v`
Expected: PASS (container starts, extension + table created, second call no-op).

- [ ] **Step 5: Commit**

```bash
git add src/axon/store/pg_vector_store.py tests/store/test_pg_vector_store.py
git commit -m "feat(pgvector): PgVectorStore skeleton + idempotent ensure_collections schema"
```

---

### Task 4: upsert / upsert_batch

**Files:**
- Modify: `src/axon/store/pg_vector_store.py`
- Test: `tests/store/test_pg_vector_store.py`

**Interfaces:**
- Consumes: `Chunk`, the pool from Task 3.
- Produces: `async upsert(chunk: Chunk)`, `async upsert_batch(chunks: list[Chunk])` - `INSERT ... ON CONFLICT (id) DO UPDATE`, one transaction per batch.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/store/test_pg_vector_store.py
def _chunk(cid: str, ctx: str = "knowledge", file_path: str = "a.py", dim: int = None):
    from axon.store.vector_store import VECTOR_SIZE, Chunk
    n = dim or VECTOR_SIZE
    return Chunk(id=cid, vector=[0.1] * n, file_path=file_path, language="python",
                 chunk_type="function", symbol="f", project="proj", ctx=ctx, content="def f(): pass")


async def test_upsert_batch_inserts_and_is_idempotent(pg_dsn) -> None:
    from axon.store.pg_vector_store import PgVectorStore
    store = PgVectorStore(dsn=pg_dsn)
    try:
        await store.ensure_collections()
        await store.upsert_batch([_chunk("id-1"), _chunk("id-2")])
        await store.upsert_batch([_chunk("id-1")])  # same id -> update, no duplicate
        async with store._pool.acquire() as con:
            count = await con.fetchval("SELECT count(*) FROM embeddings")
        assert count == 2
    finally:
        await store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_pg_vector_store.py::test_upsert_batch_inserts_and_is_idempotent -v`
Expected: FAIL (`upsert_batch` not defined).

- [ ] **Step 3: Implement upsert / upsert_batch**

Add to `PgVectorStore`:

```python
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
```

(`register_vector` from Task 3 lets asyncpg bind the `list[float]` to the `vector` column.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_pg_vector_store.py::test_upsert_batch_inserts_and_is_idempotent -v`
Expected: PASS (count == 2, re-upsert updates in place).

- [ ] **Step 5: Commit**

```bash
git add src/axon/store/pg_vector_store.py tests/store/test_pg_vector_store.py
git commit -m "feat(pgvector): upsert/upsert_batch with ON CONFLICT (idempotent re-index)"
```

---

### Task 5: search (cosine + filters + shared ranking)

**Files:**
- Modify: `src/axon/store/pg_vector_store.py`
- Test: `tests/store/test_pg_vector_store.py`

**Interfaces:**
- Consumes: pool, `_rank_and_limit` (Task 2), `_utcnow`/`datetime`.
- Produces: `async search(query_vector, collections, language=None, project=None, top_k=5, max_depth=1, max_nodes=25, max_tokens=1200) -> list[dict]`; returns `{"score","payload","id"}` dicts with `payload.modified_at` as isoformat.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/store/test_pg_vector_store.py
async def test_search_round_trip_and_ctx_filter(pg_dsn) -> None:
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_store import VECTOR_SIZE
    store = PgVectorStore(dsn=pg_dsn)
    try:
        await store.ensure_collections()
        # a "target" vector that is closest to the query
        target = _chunk("k-target", ctx="knowledge", file_path="t.py")
        target.vector = [1.0] + [0.0] * (VECTOR_SIZE - 1)
        other = _chunk("k-other", ctx="knowledge", file_path="o.py")
        other.vector = [0.0, 1.0] + [0.0] * (VECTOR_SIZE - 2)
        work = _chunk("w-secret", ctx="work", file_path="s.py")
        work.vector = [1.0] + [0.0] * (VECTOR_SIZE - 1)
        await store.upsert_batch([target, other, work])

        q = [1.0] + [0.0] * (VECTOR_SIZE - 1)
        hits = await store.search(q, collections=["knowledge"], top_k=5)
        ids = [h["id"] for h in hits]
        assert ids[0] == "k-target"          # closest first
        assert "w-secret" not in ids         # ctx filter: work never leaks into knowledge
        assert "modified_at" in hits[0]["payload"]
    finally:
        await store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_pg_vector_store.py::test_search_round_trip_and_ctx_filter -v`
Expected: FAIL (`search` not defined).

- [ ] **Step 3: Implement search**

Add to `PgVectorStore` (import `from datetime import UTC, datetime` and `from axon.store.vector_store import _rank_and_limit` at module top):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_pg_vector_store.py::test_search_round_trip_and_ctx_filter -v`
Expected: PASS (target ranked first, work filtered out, payload has modified_at).

- [ ] **Step 5: Commit**

```bash
git add src/axon/store/pg_vector_store.py tests/store/test_pg_vector_store.py
git commit -m "feat(pgvector): cosine search with ctx/language/project filters + shared ranking"
```

---

### Task 6: delete_by_file (orphan-reconcile parity)

**Files:**
- Modify: `src/axon/store/pg_vector_store.py`
- Test: `tests/store/test_pg_vector_store.py`

**Interfaces:**
- Produces: `async delete_by_file(ctx: str, file_path: str)` -> `DELETE FROM embeddings WHERE ctx=$1 AND file_path=$2`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/store/test_pg_vector_store.py
async def test_delete_by_file_removes_only_that_file(pg_dsn) -> None:
    from axon.store.pg_vector_store import PgVectorStore
    store = PgVectorStore(dsn=pg_dsn)
    try:
        await store.ensure_collections()
        await store.upsert_batch([
            _chunk("a1", ctx="knowledge", file_path="a.py"),
            _chunk("a2", ctx="knowledge", file_path="a.py"),
            _chunk("b1", ctx="knowledge", file_path="b.py"),
        ])
        await store.delete_by_file("knowledge", "a.py")
        async with store._pool.acquire() as con:
            remaining = await con.fetch("SELECT id FROM embeddings ORDER BY id")
        assert [r["id"] for r in remaining] == ["b1"]
    finally:
        await store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_pg_vector_store.py::test_delete_by_file_removes_only_that_file -v`
Expected: FAIL (`delete_by_file` not defined).

- [ ] **Step 3: Implement delete_by_file**

```python
    async def delete_by_file(self, ctx: str, file_path: str) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute("DELETE FROM embeddings WHERE ctx=$1 AND file_path=$2", ctx, file_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_pg_vector_store.py -v`
Expected: all PgVectorStore tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/axon/store/pg_vector_store.py tests/store/test_pg_vector_store.py
git commit -m "feat(pgvector): delete_by_file (per-file reconcile parity)"
```

---

### Task 7: make_vector_store() factory + repoint callers

**Files:**
- Create: `src/axon/store/vector_store_factory.py`
- Modify call sites: `src/axon/cli/pb.py` (6), `src/axon/expansion/service.py`, `src/axon/mcp/server.py`, `src/axon/obsidian/importer.py`
- Test: `tests/store/test_vector_store_factory.py`

**Interfaces:**
- Consumes: `VectorStore`, `PgVectorStore`, `load_runtime_config()`.
- Produces: `make_vector_store(runtime=None) -> VectorStore | PgVectorStore` selecting by `AXON_VECTOR_BACKEND` (default `qdrant`).

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_vector_store_factory.py
from __future__ import annotations


def test_default_is_qdrant(monkeypatch) -> None:
    monkeypatch.delenv("AXON_VECTOR_BACKEND", raising=False)
    from axon.store.vector_store import VectorStore
    from axon.store.vector_store_factory import make_vector_store
    assert isinstance(make_vector_store(), VectorStore)


def test_pgvector_selected_by_env(monkeypatch) -> None:
    monkeypatch.setenv("AXON_VECTOR_BACKEND", "pgvector")
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_store_factory import make_vector_store
    assert isinstance(make_vector_store(), PgVectorStore)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_vector_store_factory.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the factory**

```python
# src/axon/store/vector_store_factory.py
from __future__ import annotations

import os


def make_vector_store(runtime=None):
    """Select the vector backend by AXON_VECTOR_BACKEND (default 'qdrant')."""
    from axon.config.runtime import load_runtime_config

    rt = runtime or load_runtime_config()
    backend = os.environ.get("AXON_VECTOR_BACKEND", "qdrant").strip().lower()
    if backend == "pgvector":
        from axon.store.pg_vector_store import PgVectorStore

        return PgVectorStore(dsn=rt.pg_url)
    from axon.store.vector_store import VectorStore

    return VectorStore(url=rt.qdrant_url)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_vector_store_factory.py -v`
Expected: 2 passed.

- [ ] **Step 5: Repoint the call sites**

Replace each `store = VectorStore(url=_RUNTIME.qdrant_url)` (and the `self.runtime.qdrant_url` / `_QDRANT_URL` variants) with `store = make_vector_store()` and remove the now-unused local `VectorStore` import where it becomes unused. Exact sites:
- `src/axon/cli/pb.py`: lines 441, 2181, 2486, 2599, 2662, 2868.
- `src/axon/expansion/service.py`: line 518 (`make_vector_store(self.runtime)`).
- `src/axon/mcp/server.py`: line 87 (`_vector_store = make_vector_store()`).
- `src/axon/obsidian/importer.py`: line 260.
Add `from axon.store.vector_store_factory import make_vector_store` to each file's imports.

- [ ] **Step 6: Run the CLI + factory tests to confirm no breakage**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_pb_cli.py tests/store/test_vector_store_factory.py -q -p no:cacheprovider 2>&1 | tail -5`
Expected: all pass (default backend is qdrant; behavior unchanged).

- [ ] **Step 7: Commit**

```bash
git add src/axon/store/vector_store_factory.py tests/store/test_vector_store_factory.py src/axon/cli/pb.py src/axon/expansion/service.py src/axon/mcp/server.py src/axon/obsidian/importer.py
git commit -m "feat(pgvector): make_vector_store factory (AXON_VECTOR_BACKEND) + repoint callers"
```

---

### Task 8: pgvector path in the recall harness

**Files:**
- Modify: `src/axon/benchmark/recall.py`
- Test: `tests/recall/test_recall_pgvector_path.py`

**Interfaces:**
- Consumes: `make_vector_store`, `PgVectorStore`, a mock embedder (no GPU in this test).
- Produces: `index_corpus` / `run_recall_guard` parameterized so they run against the pgvector backend (an isolated table), not only the Qdrant temp collection.

- [ ] **Step 1: Read the current harness and identify the qdrant-specific seam**

The harness today builds a `QdrantClient` and a temp collection (`TEMP_COLLECTION`). Introduce a backend switch: when `AXON_VECTOR_BACKEND=pgvector`, `index_corpus` upserts through a `PgVectorStore` (into the `embeddings` table, an isolated test DB) and `run_recall_guard` searches through it; otherwise keep the Qdrant path. Keep the function signatures stable; add an internal `_make_store()` helper used by both.

- [ ] **Step 2: Write the failing test (testcontainers[postgres] + a deterministic mock embedder)**

```python
# tests/recall/test_recall_pgvector_path.py
from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer("pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon") as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_recall_harness_runs_against_pgvector(pg_dsn, monkeypatch) -> None:
    monkeypatch.setenv("AXON_VECTOR_BACKEND", "pgvector")
    monkeypatch.setenv("AXON_PG_URL", pg_dsn)
    from axon.benchmark.recall import index_corpus_pg_smoke  # thin helper added in Step 3

    # index two chunks via the pgvector path and confirm a query returns the closer one
    top_id = await index_corpus_pg_smoke(pg_dsn)
    assert top_id == "near"
```

- [ ] **Step 3: Add the backend switch + a small smoke helper**

In `src/axon/benchmark/recall.py`, add a `_make_store()` that returns `make_vector_store()` and route `index_corpus` / `run_recall_guard` through the store interface (`upsert_batch` / `search`) instead of the raw qdrant client when the backend is pgvector. Add the test-only `index_corpus_pg_smoke(dsn)` helper that ensures the schema, upserts a `near` and a `far` chunk (via `Chunk` + `PgVectorStore`), searches the `near` vector, and returns the top id. Keep the existing Qdrant path intact for the default backend.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/recall/test_recall_pgvector_path.py -v`
Expected: PASS (`near` ranked first through the pgvector store).

- [ ] **Step 5: Commit**

```bash
git add src/axon/benchmark/recall.py tests/recall/test_recall_pgvector_path.py
git commit -m "feat(recall): pgvector path in the recall harness (backend switch)"
```

---

### Task 9: Recall gate validation + final integration (release gate)

**Files:**
- No new files; runs the gate and the docker-compose path end to end.

- [ ] **Step 1: Bring up Postgres**

Run: `docker compose up -d axon-postgres` and confirm it is healthy
(`docker compose ps`). NOTE: the service is `axon-postgres` (the pgvector
service added in Task 1), NOT the `postgres` service (which is langfuse's
backend on a different port).

- [ ] **Step 2: Index the vault into pgvector**

Run: `AXON_VECTOR_BACKEND=pgvector .venv/Scripts/python.exe -m axon.cli.pb index`
Expected: completes; `SELECT count(*) FROM embeddings` is non-zero (mirror of the Qdrant point count for the same vault).

- [ ] **Step 3: Run the recall gate against pgvector (the release gate)**

Run: `AXON_VECTOR_BACKEND=pgvector AXON_RUN_RECALL=1 .venv/Scripts/python.exe -m pytest tests/recall/test_recall_guard.py -q`
Expected: PASS - no per-query rank regression and no Top-3 drop vs `tests/recall/baseline.json`. If pgvector recall is below the baseline, tune the HNSW `ef_search` (session GUC `SET hnsw.ef_search = N`) in the pgvector search path and re-run; do NOT relax the gate.

- [ ] **Step 4: Confirm Qdrant default is untouched**

Run: `.venv/Scripts/python.exe -m pytest tests/cli tests/store tests/recall -q -p no:cacheprovider 2>&1 | tail -5`
Expected: green (default backend qdrant); the pgvector tests skip when no container/docker is available.

- [ ] **Step 5: ruff + commit the gate result note**

Run: `.venv/Scripts/python.exe -m ruff check src/axon/store/pg_vector_store.py src/axon/store/vector_store_factory.py src/axon/benchmark/recall.py`
Then record the measured pgvector recall numbers in the PR description / a short note, and commit any `ef_search` tuning.

```bash
git add -A
git commit -m "chore(pgvector): recall gate green against pgvector (no regression vs baseline)"
```

---

## Notes for the executor

- The pgvector unit/integration tests require Docker (testcontainers). On a host without Docker they skip via `pytest.importorskip`; the recall gate (Task 9) requires Docker + GPU and is run on demand, not in CI.
- Do not change the Qdrant `VectorStore` behavior except the Task 2 extraction (which must be behavior-preserving - the existing tests are the guard).
- Parity is the contract: if any caller needs a method the `VectorStore` exposes that this plan missed, add it to `PgVectorStore` with the same signature rather than special-casing the caller.
