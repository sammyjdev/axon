import pytest
from fastapi.testclient import TestClient

from axon.http.app import app


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

def test_dashboard_renders_with_new_sections(client: TestClient) -> None:
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    
    # Existing gain/feed markup should still be there
    assert 'id="gain-card"' in resp.text
    assert 'id="feed-list"' in resp.text
    
    # New sections should be there
    assert 'id="activity-sessions-view"' in resp.text
    assert 'id="activity-timeline-view"' in resp.text
    assert 'id="activity-summary-view"' in resp.text
