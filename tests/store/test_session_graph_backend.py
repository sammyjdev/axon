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
