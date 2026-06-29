from __future__ import annotations


async def test_session_decisions_routes_to_postgres(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AXON_DECISIONS_BACKEND", "postgres")
    constructed = {}

    class FakePgRepo:
        def __init__(self, dsn: str) -> None:
            constructed["dsn"] = dsn

        async def ensure_schema(self) -> None:
            constructed["ensured"] = True

    monkeypatch.setattr("axon.store.pg_decision_repository.PostgresDecisionRepository", FakePgRepo)

    from axon.store.session_store import SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    repo = await store._decisions()
    assert isinstance(repo, FakePgRepo)
    assert constructed["ensured"] is True
    await store.close()
