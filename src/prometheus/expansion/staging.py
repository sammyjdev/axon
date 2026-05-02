from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from prometheus.expansion.scoring import ExpansionDecision, ExpansionScoreResult

_START = "<!-- PROMETHEUS_EXPANSION "
_END = " -->"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class StagedSource:
    title: str
    source_url: str
    published_at: str | None
    summary: str
    score: dict[str, float]
    decision: str
    reasoning: str
    evidence_quotes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ExpansionDraft:
    draft_id: str
    status: str
    ctx: str
    topic: str
    created_at: str
    updated_at: str
    staging_path: str
    publish_path: str
    risk_level: str
    recommended_action: str
    summary: str
    cloud_mode: str
    cloud_reason: str
    monthly_cloud_spend_usd: float
    telemetry_id: str
    source_count: int
    keep_count: int
    maybe_count: int
    discard_count: int
    sources: list[StagedSource]
    body: str

    def to_payload(self) -> dict[str, object]:
        return {
            **asdict(self),
            "sources": [asdict(item) for item in self.sources],
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object], body: str) -> ExpansionDraft:
        sources = [StagedSource(**item) for item in payload.get("sources", [])]
        return cls(
            draft_id=str(payload["draft_id"]),
            status=str(payload["status"]),
            ctx=str(payload["ctx"]),
            topic=str(payload["topic"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            staging_path=str(payload["staging_path"]),
            publish_path=str(payload["publish_path"]),
            risk_level=str(payload["risk_level"]),
            recommended_action=str(payload["recommended_action"]),
            summary=str(payload["summary"]),
            cloud_mode=str(payload["cloud_mode"]),
            cloud_reason=str(payload["cloud_reason"]),
            monthly_cloud_spend_usd=float(payload["monthly_cloud_spend_usd"]),
            telemetry_id=str(payload["telemetry_id"]),
            source_count=int(payload["source_count"]),
            keep_count=int(payload["keep_count"]),
            maybe_count=int(payload["maybe_count"]),
            discard_count=int(payload["discard_count"]),
            sources=sources,
            body=body,
        )


def render_draft(draft: ExpansionDraft) -> str:
    payload = json.dumps(draft.to_payload(), ensure_ascii=True, separators=(",", ":"))
    return f"{_START}{payload}{_END}\n\n{draft.body.rstrip()}\n"


def parse_draft(text: str) -> ExpansionDraft:
    line, _, body = text.partition("\n")
    if not line.startswith(_START) or not line.endswith(_END):
        raise ValueError("metadata de staging ausente")
    payload = json.loads(line[len(_START) : -len(_END)])
    return ExpansionDraft.from_payload(payload, body.lstrip("\n"))


def load_draft(path: Path) -> ExpansionDraft:
    return parse_draft(path.read_text(encoding="utf-8"))


def build_staged_sources(results: list[ExpansionScoreResult]) -> list[StagedSource]:
    return [
        StagedSource(
            title=result.candidate.title,
            source_url=result.candidate.source_url,
            published_at=result.candidate.published_at,
            summary=result.candidate.extracted_text[:500].strip(),
            score={
                "relevance": result.score.relevance,
                "novelty": result.score.novelty,
                "actionability": result.score.actionability,
                "evidence": result.score.evidence,
                "weighted_total": result.score.weighted_total,
            },
            decision=result.decision.value
            if isinstance(result.decision, ExpansionDecision)
            else str(result.decision),
            reasoning=result.reasoning,
            evidence_quotes=result.evidence_quotes,
        )
        for result in results
    ]
