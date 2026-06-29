from __future__ import annotations


async def test_session_sessions_routes_to_postgres(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AXON_SESSIONS_BACKEND", "postgres")
    constructed = {}

    class FakePgRepo:
        def __init__(self, dsn: str) -> None:
            constructed["dsn"] = dsn

        async def ensure_schema(self) -> None:
            constructed["ensured"] = True

    monkeypatch.setattr("axon.store.pg_session_repository.PostgresSessionRepository", FakePgRepo)

    from axon.store.session_store import SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    repo = await store._sessions()
    assert isinstance(repo, FakePgRepo)
    assert constructed["ensured"] is True
    await store.close()
