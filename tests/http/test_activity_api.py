import dataclasses
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer

import axon.http.app
from axon.activity.models import ActivityEvent, ActivitySession
from axon.activity.repository import PostgresActivityRepository
from axon.http.app import app


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"  # noqa: S106
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def repo(pg_dsn):
    # Setup DB state
    import asyncpg
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    async with pool.acquire() as con:
        await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await pool.close()

    r = PostgresActivityRepository(pg_dsn)
    await r.ensure_schema()
    yield r
    await r.close()


@pytest.fixture
def client(pg_dsn, repo, monkeypatch):
    monkeypatch.setattr(
        axon.http.app,
        "_RUNTIME",
        dataclasses.replace(axon.http.app._RUNTIME, pg_url=pg_dsn),
    )
    return TestClient(app)


@pytest.mark.parametrize("route", [
    "/api/activity/sessions",
    "/api/activity/search?q=x",
    "/api/activity/health",
    "/api/activity/sessions/fake-id/timeline",
])
def test_activity_routes_require_existing_dashboard_auth(route, client, monkeypatch):
    monkeypatch.setenv("AXON_HTTP_TOKEN", "secret-token")
    response = client.get(route)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_sessions_api_paginates_without_duplicate_or_missing_row(client, repo):
    for i in range(101):
        session = ActivitySession(
            session_id=f"ses-{i:03d}",
            harness="claude-code",
            source_id=f"src-{i:03d}",
            project="test-project",
            workspace="/dummy/workspace",  # noqa: S108
            status="active",
            coverage="full"
        )
        await repo.upsert_session(session)

    all_ids = set()
    cursor = None

    while True:
        url = "/api/activity/sessions?limit=10"
        if cursor is not None:
            url += f"&cursor={cursor}"
        
        response = client.get(url)
        assert response.status_code == 200
        data = response.json()
        
        for s in data["sessions"]:
            all_ids.add(s["session_id"])
            
        cursor = data.get("next_cursor")
        if not cursor:
            break

    assert len(all_ids) == 101


@pytest.mark.asyncio
async def test_unavailable_usage_is_null_not_zero(client, repo, monkeypatch):
    from axon.activity.service import ActivityService

    original_health = ActivityService.health

    async def _mock_health(self):
        report = await original_health(self)
        report.stored_bytes = None
        return report

    monkeypatch.setattr(ActivityService, "health", _mock_health)

    response = client.get("/api/activity/health")
    assert response.status_code == 200
    data = response.json()
    
    assert data["stored_bytes"] is None
    assert '"stored_bytes":null' in response.text.replace(" ", "")


@pytest.mark.asyncio
async def test_api_never_returns_secret_fixture(client, repo):
    from axon.activity.sanitize import sanitize_event
    
    raw_content = {"token": "my-super-secret-password-123"}
    sanitized_content, redactions, coverage = sanitize_event(raw_content)

    event = ActivityEvent(
        event_id="evt-secret",
        harness="claude-code",
        source_id="src-1",
        session_id="ses-1",
        occurred_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC),
        kind="test",
        content=sanitized_content,
        coverage=coverage,
        redactions=redactions
    )
    await repo.upsert_event(event)

    search_resp = client.get("/api/activity/search?q=REDACTED")
    assert search_resp.status_code == 200
    assert "my-super-secret-password-123" not in search_resp.text

    timeline_resp = client.get("/api/activity/sessions/ses-1/timeline")
    assert timeline_resp.status_code == 200
    assert "my-super-secret-password-123" not in timeline_resp.text
