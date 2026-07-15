from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed; skipping promotion routes")
pytest.importorskip("httpx", reason="httpx not installed; skipping promotion routes")

from fastapi.testclient import TestClient  # noqa: E402

from axon.http.app import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def promotion_root(tmp_path: Path) -> Path:
    manifest = {
        "run_id": "forge-executor-2026-07-12",
        "status": "published",
        "claim_ids": ["C-FORGE-EXEC-001"],
        "limitations": ["one bug family", "Python only"],
    }
    manifest_path = (
        tmp_path / "evidence/runs/forge-executor-2026-07-12/manifest.json"
    )
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "CLAIMS.md").write_text(
        "| Claim ID | Wording | Baseline | Status | Run ID | Limitation |\n"
        "|---|---|---|---|---|---|\n"
        "| C-FORGE-EXEC-001 | DeepSeek matched Sonnet 5. | Sonnet 5 | published | "
        "forge-executor-2026-07-12 | one bug family; Python only |\n",
        encoding="utf-8",
    )
    candidate = {
        "candidate_id": "P-FORGE-EXEC-001",
        "claim_id": "C-FORGE-EXEC-001",
        "run_id": "forge-executor-2026-07-12",
        "claim_ref": "CLAIMS.md#C-FORGE-EXEC-001",
        "manifest_ref": "evidence/runs/forge-executor-2026-07-12/manifest.json",
        "owner": "forge",
        "target": "models.json#legendary.exec",
        "disposition": "request-evidence",
        "scope": ["python", "executor"],
        "evidence_digest": "sha256:"
        + hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "target_digest": "sha256:" + "0" * 64,
        "evidence_requests": ["Benchmark a second Python bug family."],
    }
    (tmp_path / "promotion").mkdir()
    (tmp_path / "promotion/candidates.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-07-14T00:00:00Z",
                "candidates": [candidate],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_promotions_api_returns_valid_queue(
    client: TestClient, promotion_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_EVIDENCE_REPO", str(promotion_root))

    response = client.get("/api/promotion-candidates")

    assert response.status_code == 200
    assert response.json()["candidates"][0]["candidate_id"] == "P-FORGE-EXEC-001"


def test_promotions_api_does_not_hide_missing_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AXON_EVIDENCE_REPO", raising=False)

    response = client.get("/api/promotion-candidates")

    assert response.status_code == 404
    assert response.json()["code"] == "PROMOTION_SOURCE_NOT_CONFIGURED"


def test_promotions_dashboard_route_is_read_only(client: TestClient) -> None:
    response = client.get("/dashboard/promotions")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "read only" in response.text.lower()
