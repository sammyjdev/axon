from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_REFERENCED_MAX_BYTES = 5 * 1024 * 1024
_SOURCE_MAX_BYTES = 1024 * 1024
_Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
_Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
_PresentationText = Annotated[str, Field(min_length=1, max_length=4096)]
_EvidenceRequest = Annotated[str, Field(min_length=1, max_length=500)]
_VALID_STATUSES = {
    "published",
    "replicated",
    "preliminary",
    "near_miss",
    "inconclusive",
    "unavailable",
    "invalidated",
    "superseded",
    "archived",
}


class PromotionSourceError(Exception):
    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


class PromotionCandidateView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    claim_id: str
    run_id: str
    owner: str
    target: str
    disposition: Literal["request-evidence"]
    scope: tuple[str, ...]
    claim_status: str
    run_status: str
    wording: str
    baseline: str
    limitation: str
    run_limitations: tuple[str, ...]
    evidence_state: Literal["current", "stale"]
    target_state: Literal["unconfigured", "stale", "unsupported"]
    eligible: bool
    blockers: tuple[str, ...]
    evidence_requests: tuple[str, ...]


class PromotionCandidatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    generated_at: datetime
    observed_at: datetime
    source_state: Literal["ok"] = "ok"
    candidates: tuple[PromotionCandidateView, ...]
    errors: tuple[str, ...] = ()


class _CandidateSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: _Identifier
    claim_id: _Identifier
    run_id: _Identifier
    claim_ref: _PresentationText
    manifest_ref: _PresentationText
    owner: _Identifier
    target: _PresentationText
    disposition: Literal["request-evidence"]
    scope: tuple[_PresentationText, ...]
    evidence_digest: _Digest
    target_digest: _Digest
    evidence_requests: tuple[_EvidenceRequest, ...] = Field(max_length=10)


class _CandidatesSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    generated_at: datetime
    candidates: tuple[_CandidateSource, ...] = Field(max_length=200)


class _RunManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: _Identifier
    status: _PresentationText
    claim_ids: tuple[_Identifier, ...]
    limitations: tuple[_PresentationText, ...]


def load_promotion_candidates(
    evidence_root: Path | None = None,
    forge_root: Path | None = None,
) -> PromotionCandidatesResponse:
    """Load, validate, and project the read-only promotion queue."""
    if evidence_root is None:
        configured_root = os.getenv("AXON_EVIDENCE_REPO")
        if not configured_root:
            raise PromotionSourceError(
                404,
                "PROMOTION_SOURCE_NOT_CONFIGURED",
                "promotion evidence repository is not configured",
            )
        evidence_root = Path(configured_root)
    if forge_root is None:
        configured_forge_root = os.getenv("AXON_PROMOTION_FORGE_ROOT")
        forge_root = Path(configured_forge_root) if configured_forge_root else None

    try:
        root = evidence_root.resolve(strict=True)
        source_path = _contained_file(root, "promotion/candidates.json", _SOURCE_MAX_BYTES)
        source_bytes = source_path.read_bytes()
        raw_source = json.loads(source_bytes)
        if (
            isinstance(raw_source, dict)
            and isinstance(raw_source.get("candidates"), list)
            and len(raw_source["candidates"]) > 200
        ):
            raise PromotionSourceError(
                413, "PROMOTION_SOURCE_TOO_LARGE", "candidate count exceeds limit"
            )
        source = _CandidatesSource.model_validate(raw_source)
    except PromotionSourceError:
        raise
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise _source_error(exc) from exc

    candidate_ids = [candidate.candidate_id for candidate in source.candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise _schema_error("candidate_id values must be unique")
    owner_targets = [(candidate.owner, candidate.target) for candidate in source.candidates]
    if len(owner_targets) != len(set(owner_targets)):
        raise _schema_error("owner and target pairs must be unique")

    candidates = tuple(
        _project_candidate(root, forge_root, candidate) for candidate in source.candidates
    )
    return PromotionCandidatesResponse(
        schema_version=source.schema_version,
        generated_at=source.generated_at,
        observed_at=datetime.now(UTC),
        candidates=candidates,
    )


def _project_candidate(
    root: Path, forge_root: Path | None, candidate: _CandidateSource
) -> PromotionCandidateView:
    claim_path, separator, claim_anchor = candidate.claim_ref.partition("#")
    if not separator or claim_anchor != candidate.claim_id:
        raise _schema_error("claim_ref must identify candidate claim")
    claim = _load_claim(
        _contained_file(root, claim_path, _REFERENCED_MAX_BYTES), candidate.claim_id
    )
    manifest_path = _contained_file(root, candidate.manifest_ref, _REFERENCED_MAX_BYTES)
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = _RunManifest.model_validate_json(manifest_bytes)
    except OSError as exc:
        raise _source_error(exc) from exc
    except ValidationError as exc:
        raise _schema_error("run manifest is structurally invalid") from exc
    if (
        candidate.run_id != claim["run_id"]
        or candidate.run_id != manifest.run_id
        or candidate.claim_id not in manifest.claim_ids
    ):
        raise _schema_error("claim and run identities must agree")
    if claim["status"] not in _VALID_STATUSES or manifest.status not in _VALID_STATUSES:
        raise _schema_error("claim and run statuses must be supported")

    evidence_state = (
        "current" if _digest(manifest_bytes) == candidate.evidence_digest else "stale"
    )
    blockers = [] if evidence_state == "current" else ["EVIDENCE_STALE"]
    target_state: Literal["unconfigured", "stale", "unsupported"] = "unconfigured"
    if forge_root is not None:
        if candidate.owner == "forge" and candidate.target == "models.json#legendary.exec":
            target_path = _contained_file(forge_root, "models.json", _REFERENCED_MAX_BYTES)
            try:
                target_bytes = target_path.read_bytes()
            except OSError as exc:
                raise _source_error(exc) from exc
            if _digest(target_bytes) != candidate.target_digest:
                target_state = "stale"
                blockers.append("TARGET_STALE")
            else:
                target_state = "unsupported"
                blockers.append("TARGET_CAPABILITY_UNSUPPORTED")
        else:
            target_state = "unsupported"
            blockers.append("TARGET_CAPABILITY_UNSUPPORTED")
    else:
        blockers.append("TARGET_UNCONFIGURED")

    return PromotionCandidateView(
        candidate_id=candidate.candidate_id,
        claim_id=candidate.claim_id,
        run_id=candidate.run_id,
        owner=candidate.owner,
        target=candidate.target,
        disposition=candidate.disposition,
        scope=candidate.scope,
        claim_status=claim["status"],
        run_status=manifest.status,
        wording=claim["wording"],
        baseline=claim["baseline"],
        limitation=claim["limitation"],
        run_limitations=manifest.limitations,
        evidence_state=evidence_state,
        target_state=target_state,
        eligible=False,
        blockers=tuple(blockers),
        evidence_requests=candidate.evidence_requests,
    )


def _load_claim(path: Path, claim_id: str) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise _schema_error("claim source must be valid UTF-8") from exc
    except OSError as exc:
        raise _source_error(exc) from exc
    matches: list[list[str]] = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 6 and cells[0] == claim_id:
            matches.append(cells)
    if len(matches) != 1 or any(not cell or len(cell) > 4096 for cell in matches[0]):
        raise _schema_error("claim_ref must identify exactly one valid claim")
    fields = ("claim_id", "wording", "baseline", "status", "run_id", "limitation")
    return dict(zip(fields, matches[0]))


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _contained_file(root: Path, reference: str, max_bytes: int) -> Path:
    reference_path = Path(reference)
    if reference_path.is_absolute() or ".." in reference_path.parts:
        raise _schema_error("reference must stay inside evidence repository")
    try:
        resolved_root = root.resolve(strict=True)
        resolved = (resolved_root / reference_path).resolve(strict=True)
        size = resolved.stat().st_size
    except OSError as exc:
        raise _source_error(exc) from exc
    if not resolved.is_relative_to(resolved_root):
        raise _schema_error("reference must stay inside evidence repository")
    if size > max_bytes:
        raise PromotionSourceError(
            413, "PROMOTION_SOURCE_TOO_LARGE", "source file exceeds limit"
        )
    return resolved


def _schema_error(detail: str) -> PromotionSourceError:
    return PromotionSourceError(422, "PROMOTION_SCHEMA_INVALID", detail)


def _source_error(error: Exception) -> PromotionSourceError:
    if isinstance(error, OSError):
        return PromotionSourceError(
            503, "PROMOTION_SOURCE_UNAVAILABLE", "promotion source unavailable"
        )
    return _schema_error("promotion source is structurally invalid")
