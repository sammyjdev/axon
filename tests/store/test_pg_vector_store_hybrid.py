from __future__ import annotations

from datetime import UTC, datetime

import pytest


class _Acquire:
    def __init__(self, con):
        self._con = con

    async def __aenter__(self):
        return self._con

    async def __aexit__(self, *_exc):
        return None


class _Pool:
    def __init__(self, con):
        self._con = con

    def acquire(self):
        return _Acquire(self._con)


class _Con:
    def __init__(self, fetches=None):
        self.fetches = list(fetches or [])
        self.fetch_calls = []
        self.execute_calls = []

    async def fetch(self, sql, *params):
        self.fetch_calls.append((sql, params))
        return self.fetches.pop(0) if self.fetches else []

    async def execute(self, sql):
        self.execute_calls.append(sql)


def _record(cid: str, score: float, file_path: str | None = None, content: str = "content"):
    return {
        "id": cid,
        "file_path": file_path or f"{cid}.md",
        "language": "markdown",
        "chunk_type": "file",
        "symbol": cid,
        "project": "axon",
        "content": content,
        "git_commit": "abc",
        "modified_at": datetime(2026, 1, 1, tzinfo=UTC),
        "score": score,
    }


async def _search_with_fake_pool(monkeypatch, fetches, *, hybrid: bool, query: str | None = None):
    from axon.store.pg_vector_store import PgVectorStore

    con = _Con(fetches)
    store = PgVectorStore("postgresql://unused")

    async def fake_pool():
        return _Pool(con)

    monkeypatch.setattr(store, "_ensure_pool", fake_pool)
    if hybrid:
        monkeypatch.setenv("AXON_HYBRID_SEARCH", "1")
    else:
        monkeypatch.delenv("AXON_HYBRID_SEARCH", raising=False)
    hits = await store.search(
        query_vector=[1.0, 0.0],
        query=query,
        collections=["knowledge"],
        language="markdown",
        project="axon",
        top_k=3,
    )
    return hits, con


async def test_flag_off_uses_the_old_dense_query_shape(monkeypatch) -> None:
    hits, con = await _search_with_fake_pool(
        monkeypatch,
        [[_record("dense-1", 0.9)]],
        hybrid=False,
        query="density gate",
    )

    assert [h["id"] for h in hits] == ["dense-1"]
    assert len(con.fetch_calls) == 1
    sql, params = con.fetch_calls[0]
    assert sql.strip() == """
            SELECT id, file_path, language, chunk_type, symbol, project, content,
                   git_commit, modified_at, 1 - (vector <=> $1) AS score
            FROM embeddings
            WHERE ctx = ANY($2) AND language = $3 AND project = $4
            ORDER BY vector <=> $1
            LIMIT 3
        """.strip()
    assert params == ([1.0, 0.0], ["knowledge"], "markdown", "axon")


async def test_flag_on_issues_dense_and_lexical_queries(monkeypatch) -> None:
    hits, con = await _search_with_fake_pool(
        monkeypatch,
        [[_record("dense-1", 0.9)], [_record("lex-1", 0.7)]],
        hybrid=True,
        query="density gate",
    )

    assert [h["id"] for h in hits] == ["dense-1", "lex-1"]
    assert len(con.fetch_calls) == 2
    lexical_sql, lexical_params = con.fetch_calls[1]
    assert "websearch_to_tsquery('simple', $1)" in lexical_sql
    assert "content_tsv @@ q.query" in lexical_sql
    assert "ctx = ANY($2) AND language = $3 AND project = $4" in lexical_sql
    assert "ORDER BY ts_rank(content_tsv, q.query) DESC" in lexical_sql
    assert lexical_params == ("density gate", ["knowledge"], "markdown", "axon")


def test_rrf_math_for_item_in_both_arms() -> None:
    from axon.store.pg_vector_store import _merge_rrf_arms

    dense = [{"id": "same", "score": 0.64, "payload": {"content": "d"}}]
    lexical = [
        {"id": "x", "score": 0.9, "payload": {"content": "x"}},
        {"id": "y", "score": 0.8, "payload": {"content": "y"}},
        {"id": "same", "score": 0.7, "payload": {"content": "l"}},
    ]

    merged = _merge_rrf_arms(dense, lexical)

    same = next(item for item in merged if item["id"] == "same")
    assert same["score"] == pytest.approx((1 / 61) + (1 / 63))
    assert same["payload"]["dense_score"] == 0.64


async def test_hybrid_merge_preserves_payload_and_dedups_by_id(monkeypatch) -> None:
    hits, _con = await _search_with_fake_pool(
        monkeypatch,
        [
            [_record("same", 0.9, file_path="dense.md", content="dense payload")],
            [_record("same", 0.7, file_path="lex.md", content="lex payload")],
        ],
        hybrid=True,
        query="density gate",
    )

    assert len(hits) == 1
    assert hits[0]["id"] == "same"
    assert hits[0]["score"] == pytest.approx((1 / 61) + (1 / 61))
    assert hits[0]["payload"]["file_path"] == "dense.md"
    assert hits[0]["payload"]["dense_score"] == 0.9


async def test_hybrid_with_empty_lexical_arm_keeps_dense_ordering(monkeypatch) -> None:
    hits, _con = await _search_with_fake_pool(
        monkeypatch,
        [[_record("dense-1", 0.9), _record("dense-2", 0.8)], []],
        hybrid=True,
        query="no lexical match",
    )

    assert [(h["id"], h["score"]) for h in hits] == [("dense-1", 0.9), ("dense-2", 0.8)]
    assert "dense_score" not in hits[0]["payload"]


async def test_ensure_collections_migration_uses_if_not_exists(monkeypatch) -> None:
    from axon.store.pg_vector_store import PgVectorStore

    con = _Con()
    store = PgVectorStore("postgresql://unused")

    async def fake_pool():
        return _Pool(con)

    monkeypatch.setattr(store, "_ensure_pool", fake_pool)

    await store.ensure_collections()

    sql = "\n".join(con.execute_calls)
    assert "content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED" in sql
    assert "ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS content_tsv" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_embeddings_content_tsv" in sql
    assert "USING GIN (content_tsv)" in sql
