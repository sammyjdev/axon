# graph -> Postgres Implementation Plan (step 3, wave 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move nodes/edges from SQLite to Postgres behind a new `GraphRepository` Protocol; `SessionStore` delegates its 7 graph methods to the configured backend (`AXON_GRAPH_BACKEND`); existing graph data is copied over. Consumers and GLYPH unchanged; decisions/sessions stay SQLite.

**Architecture:** Extract the current SessionStore graph SQL into `SqliteGraphRepository` (behavior-preserving), add `PostgresGraphRepository` (asyncpg, Python BFS, ADR Option A), and have SessionStore pick the repository by config and delegate. A one-shot script copies nodes/edges (the git-derived `touches` edges are not re-index-reproducible).

**Tech Stack:** Python 3.11+, asyncpg, PostgreSQL, testcontainers[postgres], aiosqlite, pytest.

## Global Constraints

- Backend precedence EXACTLY: `AXON_GRAPH_BACKEND` env (set, non-empty) > `axon.toml [runtime] graph_backend` > default.
- `graph_backend` constrained to `{"sqlite", "postgres"}`; unknown raises `ValueError`.
- Default stays `"sqlite"` until Task 6 (gated cutover). Do NOT flip earlier.
- `RuntimeConfig.graph_backend` is a DEFAULTED trailing field (`= "sqlite"`).
- The 7 graph methods keep their EXACT signatures and return shapes; the ~9 consumer call sites and `graph_source.py` (GLYPH) are NOT modified.
- `PostgresGraphRepository` must match `SqliteGraphRepository` behavior: add_node upsert (`ON CONFLICT(id) DO UPDATE`), add_edge insert-if-absent (`ON CONFLICT (source_id,target_id,type) DO NOTHING`), payload as JSON text (`json.dumps`/`json.loads`), query_subgraph/shortest_path BFS semantics, all_nodes/all_edges ordering.
- Decisions/ADRs/sessions/memories/notes/code_changes stay on SessionStore's aiosqlite connection - do NOT touch them.
- Only plain hyphens. No live backend in unit tests except the PostgresGraphRepository conformance tests (testcontainers[postgres]).

---

### Task 1: `graph_backend` config + resolver

**Files:**
- Modify: `src/axon/config/runtime.py`
- Test: `tests/config/test_graph_backend.py`

**Interfaces:**
- Produces: `RuntimeConfig.graph_backend: str`; `_resolve_graph_backend(overrides) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_graph_backend.py
from __future__ import annotations

import pytest


def test_graph_backend_defaults_to_sqlite(monkeypatch) -> None:
    monkeypatch.delenv("AXON_GRAPH_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().graph_backend == "sqlite"


def test_graph_backend_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AXON_GRAPH_BACKEND", "postgres")
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().graph_backend == "postgres"


def test_graph_backend_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("AXON_GRAPH_BACKEND", "neo4j")
    from axon.config.runtime import load_runtime_config

    with pytest.raises(ValueError):
        load_runtime_config()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_graph_backend.py -v`
Expected: FAIL (no `graph_backend` attribute).

- [ ] **Step 3: Add field, resolver, wiring, allowlist (mirror `fileindex_backend`)**

In `src/axon/config/runtime.py`, add the defaulted trailing field:

```python
    graph_backend: str = "sqlite"
```

Add the resolver next to `_resolve_fileindex_backend`:

```python
_VALID_GRAPH_BACKENDS = ("sqlite", "postgres")


def _resolve_graph_backend(overrides: dict) -> str:
    """Select the graph backend: AXON_GRAPH_BACKEND env > axon.toml > default."""
    raw = (
        os.environ.get("AXON_GRAPH_BACKEND")
        or overrides.get("graph_backend")
        or "sqlite"
    )
    backend = raw.strip().lower()
    if backend not in _VALID_GRAPH_BACKENDS:
        raise ValueError(
            f"Invalid graph_backend {backend!r}; expected one of {list(_VALID_GRAPH_BACKENDS)}"
        )
    return backend
```

Add `graph_backend=_resolve_graph_backend(overrides),` to the `RuntimeConfig(...)` construction, and add `"graph_backend"` to the `_load_toml_runtime_overrides` allowlist.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_graph_backend.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/axon/config/runtime.py tests/config/test_graph_backend.py
git commit -m "feat(graph): RuntimeConfig.graph_backend (env > axon.toml > default sqlite), validated"
```

---

### Task 2: Extract GraphRepository Protocol + SqliteGraphRepository (behavior-preserving)

**Files:**
- Create: `src/axon/store/graph_repository.py`
- Modify: `src/axon/store/session_store.py` (the 7 graph methods delegate)
- Test: existing graph tests are the guard (find them: `grep -rl "add_node\|query_subgraph\|all_nodes" tests/`)

**Interfaces:**
- Produces: `GraphRepository(Protocol)` (7 methods); `SqliteGraphRepository(session)` holding a SessionStore ref; `SessionStore._graph()` returning the repository; the 7 SessionStore graph methods delegate.

- [ ] **Step 1: Create the Protocol + SqliteGraphRepository (move the SQL verbatim)**

Create `src/axon/store/graph_repository.py`. Define `GraphRepository(Protocol)` with the 7 method signatures (copy them from `session_store.py:338-509`: `add_node`, `add_edge`, `get_node`, `query_subgraph`, `shortest_path`, `all_nodes`, `all_edges`). Then `SqliteGraphRepository`:

```python
class SqliteGraphRepository:
    """The original SessionStore graph SQL, sharing the session's connection+lock."""

    def __init__(self, session) -> None:
        self._session = session  # SessionStore; uses _connection() and _lock
```

Move each of the 7 method bodies VERBATIM from `SessionStore` into `SqliteGraphRepository`, changing only `self._connection()` -> `self._session._connection()` and `self._lock` -> `self._session._lock`. Do not alter any SQL, JSON handling, BFS logic, or return shape. Keep `import json`, `aiosqlite`, `datetime` as needed.

- [ ] **Step 2: Make SessionStore delegate (sqlite path only this task)**

In `src/axon/store/session_store.py`, add a lazy graph-repository accessor and replace each of the 7 method bodies with a delegation. Add to `SessionStore.__init__`: `self._graph_repo = None`. Add:

```python
    async def _graph(self):
        if self._graph_repo is None:
            from axon.store.graph_repository import SqliteGraphRepository

            self._graph_repo = SqliteGraphRepository(self)
        return self._graph_repo
```

Replace each graph method body, e.g.:

```python
    async def add_node(self, node_id, node_type, *, label="", payload=None) -> None:
        repo = await self._graph()
        return await repo.add_node(node_id, node_type, label=label, payload=payload)
```

Do the same delegation for `add_edge`, `get_node`, `query_subgraph`, `shortest_path`, `all_nodes`, `all_edges` (keep their exact signatures).

- [ ] **Step 3: Run the existing graph tests to verify behavior preserved**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider -k "graph or node or edge or subgraph or session_store or shortest_path" 2>&1 | tail -8`
Expected: green (pure refactor, no behavior change). If any test referenced the inline SQL directly, fix the delegation, not the test.

- [ ] **Step 4: Commit**

```bash
git add src/axon/store/graph_repository.py src/axon/store/session_store.py
git commit -m "refactor(graph): extract SqliteGraphRepository; SessionStore delegates the 7 graph methods"
```

---

### Task 3: PostgresGraphRepository

**Files:**
- Create: `src/axon/store/pg_graph_repository.py`
- Test: `tests/store/test_pg_graph_repository.py`

**Interfaces:**
- Produces: `PostgresGraphRepository(dsn)` implementing the `GraphRepository` Protocol + `ensure_schema()` + `close()`.

- [ ] **Step 1: Write the failing test (testcontainers[postgres])**

```python
# tests/store/test_pg_graph_repository.py
from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.models import Edge  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_add_node_upsert_and_get(pg_dsn) -> None:
    from axon.store.pg_graph_repository import PostgresGraphRepository

    repo = PostgresGraphRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        await repo.ensure_schema()  # idempotent
        await repo.add_node("s1", "symbol", label="foo", payload={"k": 1})
        await repo.add_node("s1", "symbol", label="foo2", payload={"k": 2})  # upsert
        node = await repo.get_node("s1")
        assert node["label"] == "foo2" and node["payload"] == {"k": 2}
        assert await repo.get_node("missing") is None
    finally:
        await repo.close()


async def test_add_edge_idempotent_and_queries(pg_dsn) -> None:
    from axon.store.pg_graph_repository import PostgresGraphRepository

    repo = PostgresGraphRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE nodes"); await con.execute("TRUNCATE edges")
        for nid in ("a", "b", "c"):
            await repo.add_node(nid, "symbol")
        await repo.add_edge(Edge(source_id="a", target_id="b", type="touches"))
        await repo.add_edge(Edge(source_id="a", target_id="b", type="touches"))  # dup ignored
        await repo.add_edge(Edge(source_id="b", target_id="c", type="touches"))
        sub = await repo.query_subgraph("a", depth=2)
        assert sub["root"] == "a" and set(sub["nodes"]) == {"a", "b", "c"}
        assert len(sub["edges"]) == 2  # no duplicate a->b
        assert await repo.shortest_path("a", "c") == ["a", "b", "c"]
        assert await repo.shortest_path("c", "a") is None
        assert [n["id"] for n in await repo.all_nodes()] == ["a", "b", "c"]
        assert len(await repo.all_edges()) == 2
    finally:
        await repo.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_pg_graph_repository.py -v`
Expected: FAIL (module does not exist).

- [ ] **Step 3: Implement PostgresGraphRepository**

```python
# src/axon/store/pg_graph_repository.py
"""Postgres-backed GraphRepository (dec-121 step 3, wave 2).

Mirrors SqliteGraphRepository byte-for-byte: add_node upsert, add_edge
insert-if-absent, JSON-text payloads, Python BFS for query_subgraph/
shortest_path (ADR Option A - no recursive CTE), all_nodes/all_edges ordering.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import asyncpg

from axon.store.models import Edge


class PostgresGraphRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        return self._pool

    async def ensure_schema(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    id         text PRIMARY KEY,
                    type       text NOT NULL,
                    label      text NOT NULL DEFAULT '',
                    payload    text,
                    created_at text NOT NULL,
                    updated_at text NOT NULL
                )
                """
            )
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS edges (
                    source_id  text NOT NULL,
                    target_id  text NOT NULL,
                    type       text NOT NULL,
                    payload    text,
                    created_at text NOT NULL,
                    UNIQUE (source_id, target_id, type)
                )
                """
            )
            await con.execute(
                "CREATE INDEX IF NOT EXISTS ix_edges_source ON edges (source_id)"
            )

    async def add_node(self, node_id, node_type, *, label="", payload=None) -> None:
        now = datetime.now(UTC).isoformat()
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO nodes (id, type, label, payload, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (id) DO UPDATE SET
                    type=excluded.type, label=excluded.label,
                    payload=excluded.payload, updated_at=excluded.updated_at
                """,
                node_id, node_type, label, json.dumps(payload or {}), now, now,
            )

    async def add_edge(self, edge: Edge) -> None:
        now = datetime.now(UTC).isoformat()
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO edges (source_id, target_id, type, payload, created_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (source_id, target_id, type) DO NOTHING
                """,
                edge.source_id, edge.target_id, edge.type,
                json.dumps(edge.payload) if edge.payload is not None else None, now,
            )

    async def get_node(self, node_id: str) -> dict[str, object] | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT id, type, label, payload, created_at, updated_at"
                " FROM nodes WHERE id=$1",
                node_id,
            )
        if row is None:
            return None
        return {
            "id": row["id"], "type": row["type"], "label": row["label"],
            "payload": json.loads(row["payload"]) if row["payload"] else {},
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    async def query_subgraph(self, node_id: str, depth: int = 2) -> dict[str, object]:
        visited: set[str] = {node_id}
        frontier: set[str] = {node_id}
        edges: list[dict[str, str]] = []
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            for _ in range(depth):
                if not frontier:
                    break
                rows = await con.fetch(
                    "SELECT source_id, target_id, type FROM edges"
                    " WHERE source_id = ANY($1::text[])",
                    list(frontier),
                )
                next_frontier: set[str] = set()
                for row in rows:
                    edges.append(
                        {"source": row["source_id"], "target": row["target_id"], "type": row["type"]}
                    )
                    if row["target_id"] not in visited:
                        visited.add(row["target_id"])
                        next_frontier.add(row["target_id"])
                frontier = next_frontier
        return {"root": node_id, "nodes": sorted(visited), "edges": edges}

    async def shortest_path(self, from_node, to_node, max_depth: int = 10):
        if from_node == to_node:
            return [from_node]
        visited: set[str] = {from_node}
        parent: dict[str, str] = {}
        frontier: list[str] = [from_node]
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            for _ in range(max_depth):
                if not frontier:
                    break
                rows = await con.fetch(
                    "SELECT source_id, target_id FROM edges WHERE source_id = ANY($1::text[])",
                    list(frontier),
                )
                next_frontier: list[str] = []
                for row in rows:
                    target = row["target_id"]
                    if target in visited:
                        continue
                    visited.add(target)
                    parent[target] = row["source_id"]
                    if target == to_node:
                        path = [to_node]
                        while path[-1] != from_node:
                            path.append(parent[path[-1]])
                        return list(reversed(path))
                    next_frontier.append(target)
                frontier = next_frontier
        return None

    async def all_nodes(self) -> list[dict[str, object]]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch("SELECT id, type, label, payload FROM nodes ORDER BY id")
        return [
            {"id": r["id"], "type": r["type"], "label": r["label"],
             "payload": json.loads(r["payload"]) if r["payload"] else {}}
            for r in rows
        ]

    async def all_edges(self) -> list[Edge]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT source_id, target_id, type, payload FROM edges"
                " ORDER BY source_id, target_id, type"
            )
        return [
            Edge(source_id=r["source_id"], target_id=r["target_id"], type=r["type"],
                 payload=json.loads(r["payload"]) if r["payload"] else None)
            for r in rows
        ]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
```

NOTE: confirm the `Edge` import path during scouting - it may be `axon.store.models` or defined in `session_store.py`. Use the actual location.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_pg_graph_repository.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/axon/store/pg_graph_repository.py tests/store/test_pg_graph_repository.py
git commit -m "feat(graph): PostgresGraphRepository (GraphRepository Protocol over asyncpg, Python BFS, parity)"
```

---

### Task 4: SessionStore selects the graph backend

**Files:**
- Modify: `src/axon/store/session_store.py` (`_graph()` selects by config)
- Test: `tests/store/test_session_graph_backend.py`

**Interfaces:**
- Consumes: `RuntimeConfig.graph_backend`, `PostgresGraphRepository`.
- Produces: `SessionStore._graph()` returns a `PostgresGraphRepository` when `graph_backend=postgres`, else `SqliteGraphRepository`.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_session_graph_backend.py
from __future__ import annotations


async def test_session_graph_routes_to_postgres(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AXON_GRAPH_BACKEND", "postgres")
    constructed = {}

    class FakePgRepo:
        def __init__(self, dsn: str) -> None:
            constructed["dsn"] = dsn

        async def ensure_schema(self) -> None:
            constructed["ensured"] = True

    monkeypatch.setattr("axon.store.pg_graph_repository.PostgresGraphRepository", FakePgRepo)

    from axon.store.session_store import SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    repo = await store._graph()
    assert isinstance(repo, FakePgRepo)
    assert constructed["ensured"] is True
    await store.close()


async def test_session_graph_routes_to_sqlite(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AXON_GRAPH_BACKEND", "sqlite")  # pinned, survives the Task 6 flip
    from axon.store.graph_repository import SqliteGraphRepository
    from axon.store.session_store import SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    repo = await store._graph()
    assert isinstance(repo, SqliteGraphRepository)
    await store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_session_graph_backend.py -v`
Expected: FAIL (postgres test - `_graph()` always returns SqliteGraphRepository).

- [ ] **Step 3: Make `_graph()` backend-aware**

In `src/axon/store/session_store.py`, resolve the backend once (read `AXON_GRAPH_BACKEND` via the resolver, and `pg_url` from config) and branch in `_graph()`:

```python
    async def _graph(self):
        if self._graph_repo is None:
            from axon.config.runtime import load_runtime_config

            rt = load_runtime_config()
            if rt.graph_backend == "postgres":
                from axon.store.pg_graph_repository import PostgresGraphRepository

                self._graph_repo = PostgresGraphRepository(rt.pg_url)
                await self._graph_repo.ensure_schema()
            else:
                from axon.store.graph_repository import SqliteGraphRepository

                self._graph_repo = SqliteGraphRepository(self)
        return self._graph_repo
```

This uses `load_runtime_config().graph_backend`, which already applies the env >
axon.toml > default precedence (Task 1), so both the env var and the toml value
are honored. Also close the Postgres repo in `SessionStore.close()`:

```python
        if self._graph_repo is not None and hasattr(self._graph_repo, "close"):
            await self._graph_repo.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_session_graph_backend.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/axon/store/session_store.py tests/store/test_session_graph_backend.py
git commit -m "feat(graph): SessionStore._graph() selects repository by graph_backend"
```

---

### Task 5: data-copy script

**Files:**
- Create: `scripts/migrate_graph.py`
- Test: `tests/scripts/test_migrate_graph.py`

**Interfaces:**
- Produces: `copy_graph(src_repo, dst_repo) -> tuple[int, int]` (nodes, edges copied), idempotent.

- [ ] **Step 1: Write the failing test**

```python
# tests/scripts/test_migrate_graph.py
from __future__ import annotations


class _FakeRepo:
    def __init__(self, nodes=None, edges=None):
        self._nodes = nodes or []
        self._edges = edges or []
        self.added_nodes = []
        self.added_edges = []

    async def all_nodes(self):
        return self._nodes

    async def all_edges(self):
        return self._edges

    async def add_node(self, node_id, node_type, *, label="", payload=None):
        self.added_nodes.append(node_id)

    async def add_edge(self, edge):
        self.added_edges.append((edge.source_id, edge.target_id, edge.type))


async def test_copy_graph_counts() -> None:
    from axon.store.models import Edge
    from scripts.migrate_graph import copy_graph

    src = _FakeRepo(
        nodes=[{"id": "a", "type": "symbol", "label": "A", "payload": {}}],
        edges=[Edge(source_id="a", target_id="b", type="touches", payload=None)],
    )
    dst = _FakeRepo()
    n, e = await copy_graph(src, dst)
    assert (n, e) == (1, 1)
    assert dst.added_nodes == ["a"]
    assert dst.added_edges == [("a", "b", "touches")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/scripts/test_migrate_graph.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the copy script**

```python
# scripts/migrate_graph.py
"""One-shot copy of the code graph (nodes/edges) from SQLite to Postgres.

Idempotent: add_node upserts, add_edge ignores duplicates. Preserves the
git-derived 'touches' edges that a re-index would not reproduce.
"""
from __future__ import annotations


async def copy_graph(src_repo, dst_repo) -> tuple[int, int]:
    nodes = await src_repo.all_nodes()
    for n in nodes:
        await dst_repo.add_node(
            n["id"], n["type"], label=n.get("label", ""), payload=n.get("payload") or {}
        )
    edges = await src_repo.all_edges()
    for e in edges:
        await dst_repo.add_edge(e)
    return len(nodes), len(edges)


async def _main() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from axon.config.runtime import load_runtime_config
    from axon.store.graph_repository import SqliteGraphRepository
    from axon.store.pg_graph_repository import PostgresGraphRepository
    from axon.store.session_store import SessionStore

    rt = load_runtime_config()
    session = SessionStore(db_path=rt.db_path)
    await session.init()
    src = SqliteGraphRepository(session)
    dst = PostgresGraphRepository(rt.pg_url)
    try:
        await dst.ensure_schema()
        n, e = await copy_graph(src, dst)
        print(f"copied {n} nodes, {e} edges -> postgres")
    finally:
        await session.close()
        await dst.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/scripts/test_migrate_graph.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_graph.py tests/scripts/test_migrate_graph.py
git commit -m "feat(graph): one-shot SQLite->Postgres graph copy script (idempotent)"
```

---

### Task 6: Cutover - copy data, validate, flip the default (controller-run)

**Files:**
- Modify: `src/axon/config/runtime.py` (`_resolve_graph_backend` default)
- Modify: `tests/config/test_graph_backend.py`
- Docs: `docs/MIGRATION.md`

GATED on operator-run validation (needs Postgres + the real SQLite graph). Validate FIRST.

- [ ] **Step 1: Acceptance gate (operator-run)**

```bash
docker compose up -d axon-postgres
PYTHONPATH=src .venv/Scripts/python.exe scripts/migrate_graph.py   # copy
# count parity:
docker compose exec -T axon-postgres psql -U axon -d axon -tAc "SELECT (SELECT count(*) FROM nodes), (SELECT count(*) FROM edges);"
# compare to SQLite (expect equal node/edge counts).
# GLYPH parity: a graph query returns the same neighborhood:
AXON_GRAPH_BACKEND=postgres PYTHONPATH=src .venv/Scripts/python.exe -m axon.cli.pb graph subgraph <some-node-id> --depth 2
```
Proceed only if counts match and the subgraph matches the sqlite result. Else STOP.

- [ ] **Step 2: Update the default test (RED)**

In `tests/config/test_graph_backend.py`, change `test_graph_backend_defaults_to_sqlite` to expect postgres and rename to `test_graph_backend_defaults_to_postgres`.

- [ ] **Step 3: Run to verify it FAILS**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_graph_backend.py::test_graph_backend_defaults_to_postgres -v`
Expected: FAIL.

- [ ] **Step 4: Flip the default**

In `_resolve_graph_backend`, change the fallback `"sqlite"` to `"postgres"` (leave the `RuntimeConfig.graph_backend = "sqlite"` field default).

- [ ] **Step 5: Run + sweep**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_graph_backend.py tests/store tests/cli -q -p no:cacheprovider 2>&1 | tail -6`
Expected: green. Any test that exercised the graph via the default sqlite backend and now hits postgres should be pinned to `graph_backend=sqlite` (mock the resolver/env) - same pattern as the file_index migration-test pin. Fix such fallout.

- [ ] **Step 6: Runbook + commit**

Add a `docs/MIGRATION.md` graph-wave note: copy via `scripts/migrate_graph.py` (preserves git-derived edges); flip `graph_backend = "postgres"`; rollback = `sqlite` (SQLite graph intact); GLYPH unchanged (Option A).

```bash
git add src/axon/config/runtime.py tests/config/test_graph_backend.py docs/MIGRATION.md
git commit -m "feat(graph): cutover - default graph backend is now postgres (sqlite via override/rollback)"
```

---

## Notes for the executor

- Tasks 1-5 are autonomous; Task 6 Step 1 is operator-run (needs Postgres + the real graph), the flip (Steps 2-6) follows only after it passes.
- Task 2 is a PURE refactor - the existing graph tests are the guard; do not change behavior. Confirm the `Edge` import path during scouting.
- Do NOT modify the ~9 consumer call sites or `graph_source.py` - they call the same SessionStore methods, which now delegate.
- Do NOT touch SessionStore's decisions/sessions/memories/notes/code_changes - they stay on aiosqlite this wave.
- The default flip is the LAST change; until then graph_backend defaults to sqlite.
