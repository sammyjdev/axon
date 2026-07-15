from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from axon.promotion import PromotionSourceError, load_promotion_candidates


@pytest.fixture
def promotion_fixture(tmp_path: Path) -> Path:
    manifest = {
        "run_id": "forge-executor-2026-07-12",
        "status": "published",
        "claim_ids": ["C-FORGE-EXEC-001"],
        "limitations": ["one bug family", "Python only"],
    }
    manifest_path = (
        tmp_path / "evidence" / "runs" / "forge-executor-2026-07-12" / "manifest.json"
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
        "evidence_digest": "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "target_digest": "sha256:" + "0" * 64,
        "evidence_requests": ["Benchmark a second Python bug family."],
    }
    (tmp_path / "promotion").mkdir()
    (tmp_path / "promotion" / "candidates.json").write_text(
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


def rewrite_candidate(root: Path, **changes: object) -> None:
    path = root / "promotion" / "candidates.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["candidates"][0].update(changes)
    path.write_text(json.dumps(payload), encoding="utf-8")


def rewrite_manifest(root: Path, **changes: object) -> None:
    path = root / "evidence" / "runs" / "forge-executor-2026-07-12" / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(changes)
    path.write_text(json.dumps(payload), encoding="utf-8")
    rewrite_candidate(
        root, evidence_digest="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    )


def rewrite_claim_status(root: Path, status: str) -> None:
    path = root / "CLAIMS.md"
    content = path.read_text(encoding="utf-8")
    path.write_text(
        content.replace("| published | forge-executor", f"| {status} | forge-executor"),
        encoding="utf-8",
    )


def test_loads_request_evidence_candidate(promotion_fixture: Path) -> None:
    response = load_promotion_candidates(promotion_fixture)

    candidate = response.candidates[0]
    assert candidate.candidate_id == "P-FORGE-EXEC-001"
    assert candidate.disposition == "request-evidence"
    assert candidate.claim_status == "published"
    assert candidate.run_status == "published"
    assert candidate.eligible is False
    assert candidate.target_state == "unconfigured"


def test_parent_reference_is_schema_error(promotion_fixture: Path) -> None:
    rewrite_candidate(promotion_fixture, manifest_ref="../secret.json")

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert raised.value.status_code == 422
    assert raised.value.code == "PROMOTION_SCHEMA_INVALID"


def test_stale_manifest_is_visible_not_empty(promotion_fixture: Path) -> None:
    rewrite_candidate(promotion_fixture, evidence_digest="sha256:" + "0" * 64)

    response = load_promotion_candidates(promotion_fixture)

    assert response.candidates[0].evidence_state == "stale"
    assert response.candidates[0].eligible is False


def test_missing_environment_configuration_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AXON_EVIDENCE_REPO", raising=False)

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates()

    assert (raised.value.status_code, raised.value.code) == (
        404,
        "PROMOTION_SOURCE_NOT_CONFIGURED",
    )


def test_missing_source_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(tmp_path / "missing")

    assert (raised.value.status_code, raised.value.code) == (
        503,
        "PROMOTION_SOURCE_UNAVAILABLE",
    )


def test_duplicate_candidate_ids_are_schema_error(promotion_fixture: Path) -> None:
    path = promotion_fixture / "promotion" / "candidates.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["candidates"].append(payload["candidates"][0])
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert raised.value.status_code == 422


def test_candidate_source_size_limit_is_enforced(promotion_fixture: Path) -> None:
    path = promotion_fixture / "promotion" / "candidates.json"
    path.write_bytes(b" " * (1024 * 1024 + 1))

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert (raised.value.status_code, raised.value.code) == (
        413,
        "PROMOTION_SOURCE_TOO_LARGE",
    )


def test_target_digest_mismatch_is_visible_as_stale(
    promotion_fixture: Path, tmp_path: Path
) -> None:
    forge_root = tmp_path / "forge"
    forge_root.mkdir()
    (forge_root / "models.json").write_text("{}", encoding="utf-8")

    response = load_promotion_candidates(promotion_fixture, forge_root)

    assert response.candidates[0].target_state == "stale"
    assert response.candidates[0].eligible is False


def test_matching_known_target_remains_unsupported(
    promotion_fixture: Path, tmp_path: Path
) -> None:
    forge_root = tmp_path / "forge"
    forge_root.mkdir()
    target = forge_root / "models.json"
    target.write_text("{}", encoding="utf-8")
    rewrite_candidate(
        promotion_fixture,
        target_digest="sha256:" + hashlib.sha256(target.read_bytes()).hexdigest(),
    )

    response = load_promotion_candidates(promotion_fixture, forge_root)

    assert response.candidates[0].target_state == "unsupported"
    assert "TARGET_CAPABILITY_UNSUPPORTED" in response.candidates[0].blockers


@pytest.mark.parametrize(
    "target",
    [
        "/etc/passwd",
        "C:/Windows/system.ini",
        "../secret",
        "models.json#../secret",
        r"..\secret",
        r"models.json#..\secret",
    ],
)
def test_unsafe_unsupported_target_is_rejected_without_serialized_leak(
    promotion_fixture: Path, target: str
) -> None:
    rewrite_candidate(promotion_fixture, owner="other", target=target)

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert (raised.value.status_code, raised.value.code) == (
        422,
        "PROMOTION_SCHEMA_INVALID",
    )
    assert target not in json.dumps(raised.value.__dict__)


def test_malformed_digest_is_schema_error(promotion_fixture: Path) -> None:
    rewrite_candidate(promotion_fixture, evidence_digest="sha256:nope")

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert raised.value.status_code == 422


@pytest.mark.parametrize("payload", [b'{"schema_version":', b"not json"])
def test_truncated_or_invalid_json_is_schema_error(
    promotion_fixture: Path, payload: bytes
) -> None:
    (promotion_fixture / "promotion" / "candidates.json").write_bytes(payload)

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert raised.value.status_code == 422


def test_invalid_utf8_candidate_source_is_schema_error(promotion_fixture: Path) -> None:
    (promotion_fixture / "promotion" / "candidates.json").write_bytes(b"\xff")

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert (raised.value.status_code, raised.value.code) == (
        422,
        "PROMOTION_SCHEMA_INVALID",
    )


def test_unknown_candidate_field_is_schema_error(promotion_fixture: Path) -> None:
    rewrite_candidate(promotion_fixture, surprise=True)

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert raised.value.status_code == 422


def test_claim_and_run_identity_divergence_is_schema_error(
    promotion_fixture: Path,
) -> None:
    rewrite_candidate(promotion_fixture, run_id="another-run")

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert raised.value.status_code == 422


def test_unknown_manifest_field_is_schema_error(promotion_fixture: Path) -> None:
    rewrite_manifest(promotion_fixture, surprise=True)

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert (raised.value.status_code, raised.value.code) == (
        422,
        "PROMOTION_SCHEMA_INVALID",
    )


def test_claim_and_run_status_divergence_remains_visible(promotion_fixture: Path) -> None:
    rewrite_manifest(promotion_fixture, status="preliminary")

    candidate = load_promotion_candidates(promotion_fixture).candidates[0]

    assert candidate.claim_status == "published"
    assert candidate.run_status == "preliminary"
    assert candidate.eligible is False


@pytest.mark.parametrize(
    "status",
    [
        "published",
        "replicated",
        "preliminary",
        "near_miss",
        "inconclusive",
        "not_comparable",
    ],
)
def test_request_evidence_accepts_v1_reviewable_statuses(
    promotion_fixture: Path, status: str
) -> None:
    rewrite_claim_status(promotion_fixture, status)

    candidate = load_promotion_candidates(promotion_fixture).candidates[0]

    assert candidate.claim_status == status
    assert candidate.disposition == "request-evidence"


@pytest.mark.parametrize(
    "status", ["unavailable", "invalidated", "superseded", "archived"]
)
def test_request_evidence_rejects_v1_terminal_statuses(
    promotion_fixture: Path, status: str
) -> None:
    rewrite_claim_status(promotion_fixture, status)

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert (raised.value.status_code, raised.value.code) == (
        422,
        "PROMOTION_SCHEMA_INVALID",
    )


def test_referenced_source_size_limit_is_enforced(promotion_fixture: Path) -> None:
    manifest = (
        promotion_fixture
        / "evidence"
        / "runs"
        / "forge-executor-2026-07-12"
        / "manifest.json"
    )
    manifest.write_bytes(b" " * (5 * 1024 * 1024 + 1))

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert raised.value.status_code == 413


def test_symlink_escape_is_schema_error(promotion_fixture: Path) -> None:
    manifest = (
        promotion_fixture
        / "evidence"
        / "runs"
        / "forge-executor-2026-07-12"
        / "manifest.json"
    )
    outside = promotion_fixture.parent / f"{promotion_fixture.name}-outside.json"
    outside.write_text("{}", encoding="utf-8")
    manifest.unlink()
    manifest.symlink_to(outside)

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert raised.value.status_code == 422


def test_candidate_count_limit_is_enforced(promotion_fixture: Path) -> None:
    path = promotion_fixture / "promotion" / "candidates.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    template = payload["candidates"][0]
    payload["candidates"] = [
        {**template, "candidate_id": f"P-{index}", "target": f"target-{index}"}
        for index in range(201)
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert (raised.value.status_code, raised.value.code) == (
        413,
        "PROMOTION_SOURCE_TOO_LARGE",
    )


def test_duplicate_owner_target_is_schema_error(promotion_fixture: Path) -> None:
    path = promotion_fixture / "promotion" / "candidates.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["candidates"].append(
        {**payload["candidates"][0], "candidate_id": "P-FORGE-EXEC-002"}
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PromotionSourceError) as raised:
        load_promotion_candidates(promotion_fixture)

    assert raised.value.status_code == 422


def test_serialized_response_does_not_expose_absolute_paths(
    promotion_fixture: Path,
) -> None:
    serialized = load_promotion_candidates(promotion_fixture).model_dump_json()

    assert str(promotion_fixture) not in serialized
    assert "file://" not in serialized
