from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import litellm

from prometheus.config.runtime import RuntimeConfig, load_runtime_config
from prometheus.context.registry import VALID_CONTEXTS as REGISTERED_CONTEXTS
from prometheus.expansion.budget import BudgetEnforcement, ExpansionBudgetManager
from prometheus.expansion.collector import ExpansionCollector
from prometheus.expansion.models import SourceDocument
from prometheus.expansion.registry import SourceRegistry, default_source_registry
from prometheus.expansion.scoring import ExpansionCandidate, ExpansionDecision, score_candidates
from prometheus.expansion.staging import (
    ExpansionDraft,
    build_staged_sources,
    load_draft,
    render_draft,
)
from prometheus.expansion.telemetry import ExpansionExecutionRecord, ExpansionTelemetryStore
from prometheus.expansion.transport import SourceTransport
from prometheus.policy.core import PolicyRegistry

_TITLE_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
_CREATED_RE = re.compile(r"^\s*created:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", re.MULTILINE)
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)
_SUPPORTED_SUFFIXES = {".md", ".txt"}
_VALID_CONTEXTS = set(REGISTERED_CONTEXTS)
_CLOUD_COST_PER_1K = {
    "claude-haiku-4-5-20251001": 0.001,
    "claude-sonnet-4-6": 0.01,
    "claude-opus-4-7": 0.05,
}


@dataclass(frozen=True)
class ReviewGate:
    risk_level: str
    recommended_action: str
    summary: str


class ExpansionService:
    def __init__(
        self,
        runtime: RuntimeConfig | None = None,
        *,
        source_registry: SourceRegistry | None = None,
        collector_transport: SourceTransport | None = None,
    ) -> None:
        self.runtime = runtime or load_runtime_config()
        self.budget = ExpansionBudgetManager(self.runtime)
        self.telemetry = ExpansionTelemetryStore(self.runtime)
        self.policy = PolicyRegistry(self.runtime)
        self.source_registry = source_registry or default_source_registry(runtime=self.runtime)
        self.collector = ExpansionCollector(
            registry=self.source_registry,
            transport=collector_transport,
        )

    def run(self, *, ctx: str, topic: str, fast: bool, allow_cloud: bool) -> Path:
        normalized_ctx = ctx.strip().lower()
        self._validate_context(normalized_ctx)

        budget_status = self.budget.status()
        cloud_mode, cloud_reason = self._resolve_cloud_mode(
            normalized_ctx,
            allow_cloud,
            budget_status.enforcement,
        )
        candidates = self._collect_local_candidates(normalized_ctx, topic, fast=fast)
        web_documents = asyncio.run(self._collect_web_documents(normalized_ctx, fast=fast))
        candidates.extend(
            self._web_document_to_candidate(document)
            for document in self._filter_web_documents(web_documents, topic, fast=fast)
        )
        candidates = self._dedupe_candidates(candidates)
        scored = score_candidates(candidates, topic)
        cloud_review = self._empty_cloud_review()
        if cloud_mode == "cloud_allowed" and self._should_run_cloud_review(scored, web_documents):
            cloud_review = asyncio.run(self._run_cloud_review(normalized_ctx, topic, scored))
            if cloud_review["cost_usd"] > 0:
                budget_status = self.budget.record_usage(
                    cloud_review["budget_record"],
                )
        gate = self._review_gate(scored, cloud_review["summary"])

        draft_id = str(uuid.uuid4())
        staging_path = (
            self.runtime.vault_context_root(normalized_ctx) / "staging" / f"{_slugify(topic)}.md"
        )
        publish_path = (
            self.runtime.vault_context_root(normalized_ctx) / "expansion" / f"{_slugify(topic)}.md"
        )
        now = _utc_now()

        draft = ExpansionDraft(
            draft_id=draft_id,
            status="pending_review",
            ctx=normalized_ctx,
            topic=topic,
            created_at=now,
            updated_at=now,
            staging_path=str(staging_path),
            publish_path=str(publish_path),
            risk_level=gate.risk_level,
            recommended_action=gate.recommended_action,
            summary=gate.summary,
            cloud_mode=cloud_mode,
            cloud_reason=self._cloud_reason_with_details(cloud_reason, cloud_review["summary"]),
            monthly_cloud_spend_usd=budget_status.spent_usd,
            telemetry_id=draft_id,
            source_count=len(scored),
            keep_count=sum(1 for item in scored if item.decision is ExpansionDecision.KEEP),
            maybe_count=sum(1 for item in scored if item.decision is ExpansionDecision.MAYBE),
            discard_count=sum(1 for item in scored if item.decision is ExpansionDecision.DISCARD),
            sources=build_staged_sources(scored),
            body=self._render_body(topic, gate, scored, cloud_review["summary"]),
        )
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        staging_path.write_text(render_draft(draft), encoding="utf-8")
        self.telemetry.append(
            ExpansionExecutionRecord(
                execution_id=draft_id,
                ctx=normalized_ctx,
                topic=topic,
                mode="fast" if fast else "full",
                status="staged",
                used_cloud=cloud_mode == "cloud_allowed",
                cloud_cost_usd=cloud_review["cost_usd"],
                staging_path=str(staging_path),
                metadata={
                    "recommended_action": gate.recommended_action,
                    "risk_level": gate.risk_level,
                    "source_count": len(scored),
                    "web_source_count": len(web_documents),
                },
            )
        )
        return staging_path

    def review(self, staging_path: Path) -> ExpansionDraft:
        return self._load_valid_staging_path(staging_path)

    def approve(self, staging_path: Path) -> tuple[Path, str]:
        draft = self._load_valid_staging_path(staging_path)
        publish_path = self._expected_publish_path(draft.ctx, draft.topic)
        publish_path.parent.mkdir(parents=True, exist_ok=True)
        publish_path.write_text(self._render_final_markdown(draft), encoding="utf-8")

        updated = ExpansionDraft.from_payload(
            {
                **draft.to_payload(),
                "status": "approved",
                "updated_at": _utc_now(),
                "publish_path": str(publish_path),
                "summary": f"{draft.summary} (published)",
            },
            draft.body,
        )
        staging_path.write_text(render_draft(updated), encoding="utf-8")
        reindex_status = "reindex_ok"
        try:
            asyncio.run(self._reindex_publish_path(publish_path, draft.ctx))
        except Exception:
            reindex_status = "reindex_skipped"
        self.telemetry.append(
            ExpansionExecutionRecord(
                execution_id=draft.telemetry_id,
                ctx=draft.ctx,
                topic=draft.topic,
                mode="approve",
                status="approved",
                used_cloud=draft.cloud_mode == "cloud_allowed",
                cloud_cost_usd=0.0,
                staging_path=str(staging_path),
                metadata={
                    "publish_path": str(publish_path),
                    "reindex_status": reindex_status,
                },
            )
        )
        return publish_path, reindex_status

    def reject(self, staging_path: Path) -> Path:
        draft = self._load_valid_staging_path(staging_path)
        rejected_path = (
            self.runtime.vault_context_root(draft.ctx) / "staging" / "rejected" / staging_path.name
        )
        rejected_path.parent.mkdir(parents=True, exist_ok=True)
        updated = ExpansionDraft.from_payload(
            {
                **draft.to_payload(),
                "status": "rejected",
                "updated_at": _utc_now(),
                "summary": f"{draft.summary} (rejected)",
            },
            draft.body,
        )
        rejected_path.write_text(render_draft(updated), encoding="utf-8")
        staging_path.unlink()
        self.telemetry.append(
            ExpansionExecutionRecord(
                execution_id=draft.telemetry_id,
                ctx=draft.ctx,
                topic=draft.topic,
                mode="reject",
                status="rejected",
                used_cloud=draft.cloud_mode == "cloud_allowed",
                cloud_cost_usd=0.0,
                staging_path=str(rejected_path),
            )
        )
        return rejected_path

    @staticmethod
    def format_review(draft: ExpansionDraft) -> str:
        return (
            f"topic={draft.topic}\n"
            f"ctx={draft.ctx}\n"
            f"status={draft.status}\n"
            f"risk_level={draft.risk_level}\n"
            f"recommended_action={draft.recommended_action}\n"
            f"sources={draft.source_count} keep={draft.keep_count} "
            f"maybe={draft.maybe_count} discard={draft.discard_count}\n"
            f"cloud_mode={draft.cloud_mode} reason={draft.cloud_reason} "
            f"month_spend=${draft.monthly_cloud_spend_usd:.2f}\n"
            f"publish_path={draft.publish_path}\n"
            f"summary={draft.summary}"
        )

    def _validate_context(self, ctx: str) -> None:
        if ctx not in _VALID_CONTEXTS:
            raise ValueError(f"contexto invalido: {ctx}")
        if self.runtime.expansion.manual_trigger_only is not True:
            raise ValueError("expansion deve permanecer manual trigger only")
        if ctx != "work" and ctx not in self.runtime.expansion.default_contexts:
            raise ValueError(f"expansion desabilitada para ctx={ctx}")

    def _load_valid_staging_path(self, staging_path: Path) -> ExpansionDraft:
        if not staging_path.exists():
            raise FileNotFoundError(staging_path)
        resolved_path = staging_path.resolve()
        try:
            relative = resolved_path.relative_to(self.runtime.vault_root.resolve())
        except ValueError as exc:
            raise ValueError("arquivo fora da area de staging") from exc
        if len(relative.parts) < 3 or relative.parts[1] != "staging":
            raise ValueError("arquivo fora da area de staging")
        if relative.parts[2] == "rejected":
            raise ValueError("arquivo fora da area de staging")

        ctx_from_path = relative.parts[0].strip().lower()
        self._validate_context(ctx_from_path)

        draft = load_draft(resolved_path)
        draft_ctx = draft.ctx.strip().lower()
        if draft_ctx != ctx_from_path:
            raise ValueError("ctx do staging diverge do diretorio")
        return draft

    def _expected_publish_path(self, ctx: str, topic: str) -> Path:
        return self.runtime.vault_context_root(ctx) / "expansion" / f"{_slugify(topic)}.md"

    def _collect_local_candidates(
        self, ctx: str, topic: str, *, fast: bool
    ) -> list[ExpansionCandidate]:
        root = self.runtime.vault_context_root(ctx)
        if not root.exists():
            return []
        ranked: list[tuple[float, ExpansionCandidate]] = []
        limit = 8 if fast else 16
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _SUPPORTED_SUFFIXES:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            score = _topic_overlap(topic, content, path.name)
            if score <= 0:
                continue
            ranked.append((score, self._candidate_from_file(path, content)))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [candidate for _, candidate in ranked[:limit]]

    def _candidate_from_file(self, path: Path, content: str) -> ExpansionCandidate:
        title_match = _TITLE_RE.search(content)
        created_match = _CREATED_RE.search(content)
        relative = path.relative_to(self.runtime.vault_root).as_posix()
        excerpt = _excerpt(content)
        return ExpansionCandidate(
            title=title_match.group(1).strip() if title_match else path.stem.replace("-", " "),
            extracted_text=excerpt,
            source_url=f"vault://{relative}",
            published_at=created_match.group(1) if created_match else None,
        )

    def _review_gate(self, scored, cloud_summary: str | None = None) -> ReviewGate:
        if not scored:
            return ReviewGate(
                risk_level="high",
                recommended_action="reject",
                summary=self._merge_summaries(
                    "Nenhuma fonte registrada relevante foi encontrada no contexto selecionado.",
                    cloud_summary,
                ),
            )
        keep_count = sum(1 for item in scored if item.decision is ExpansionDecision.KEEP)
        maybe_count = sum(1 for item in scored if item.decision is ExpansionDecision.MAYBE)
        if keep_count >= 2:
            return ReviewGate(
                risk_level="low",
                recommended_action="approve",
                summary=self._merge_summaries(
                    f"{keep_count} fontes fortes encontradas; pronto para publicar apos review.",
                    cloud_summary,
                ),
            )
        if keep_count >= 1 or maybe_count >= 2:
            return ReviewGate(
                risk_level="medium",
                recommended_action="review",
                summary=self._merge_summaries(
                    "Ha material promissor, mas a revisao humana ainda e obrigatoria.",
                    cloud_summary,
                ),
            )
        return ReviewGate(
            risk_level="high",
            recommended_action="reject",
            summary=self._merge_summaries(
                "As fontes coletadas nao atingiram sinal suficiente para publicacao.",
                cloud_summary,
            ),
        )

    def _render_body(
        self,
        topic: str,
        gate: ReviewGate,
        scored,
        cloud_summary: str | None,
    ) -> str:
        if scored:
            items = "\n".join(
                f"- {item.candidate.title} [{item.decision.value}] "
                f"total={item.score.weighted_total:.2f} source={item.candidate.source_url}"
                for item in scored[:6]
            )
            details = "\n\n".join(
                (
                    f"### {item.candidate.title}\n"
                    f"Source: {item.candidate.source_url}\n"
                    f"Published: {item.candidate.published_at or 'unknown'}\n"
                    f"Reasoning: {item.reasoning}\n\n"
                    f"{item.candidate.extracted_text}"
                )
                for item in scored[:4]
            )
        else:
            items = "- Nenhuma fonte passou pela coleta local."
            details = "Nenhuma fonte coletada."
        cloud_section = ""
        if cloud_summary:
            cloud_section = f"\n## Cloud Review\n{cloud_summary}\n"
        return (
            f"# Expansion Draft: {topic}\n\n"
            "## Review Gate\n"
            f"- risk_level: {gate.risk_level}\n"
            f"- recommended_action: {gate.recommended_action}\n"
            f"- summary: {gate.summary}\n\n"
            "## Candidate Summary\n"
            f"{items}\n\n"
            "## Evidence\n"
            f"{details}\n"
            f"{cloud_section}"
        )

    def _render_final_markdown(self, draft: ExpansionDraft) -> str:
        sources = (
            "\n".join(f"- {source.title} ({source.source_url})" for source in draft.sources[:8])
            or "- Nenhuma fonte"
        )
        return (
            "---\n"
            f"created: {draft.created_at[:10]}\n"
            "type: expansion\n"
            f"topic: {draft.topic}\n"
            f"risk_level: {draft.risk_level}\n"
            "verified: false\n"
            f"source_count: {draft.source_count}\n"
            "---\n\n"
            f"# Expansion: {draft.topic}\n\n"
            f"{draft.summary}\n\n"
            "## Sources\n"
            f"{sources}\n\n"
            "## Draft\n"
            f"{draft.body.strip()}\n"
        )

    async def _reindex_publish_path(self, publish_path: Path, ctx: str) -> None:
        from prometheus.embedder.engine import EmbedderEngine
        from prometheus.embedder.pipeline import index_path
        from prometheus.store.vector_store import VectorStore

        engine = EmbedderEngine()
        store = VectorStore(url=self.runtime.qdrant_url)
        try:
            await store.ensure_collections()
            await index_path(
                publish_path,
                engine=engine,
                store=store,
                vault_root=self.runtime.vault_root,
                forced_ctx=ctx,
            )
        finally:
            await store.close()

    def _resolve_cloud_mode(
        self,
        ctx: str,
        allow_cloud: bool,
        enforcement: BudgetEnforcement,
    ) -> tuple[str, str]:
        if not allow_cloud:
            return "local_only", "cloud disabled by caller"
        decision = self.policy.decide(
            ctx=ctx,
            model=self.runtime.classifier_cloud_model,
            caller="expand",
        )
        if not decision.allowed:
            return "local_only", decision.reason_code.value
        if enforcement is BudgetEnforcement.HARD_STOP:
            return "local_only", "hard cap reached"
        if enforcement is BudgetEnforcement.LOCAL_ONLY:
            return "local_only", "soft cap reached"
        return "cloud_allowed", "budget available"

    async def _collect_web_documents(self, ctx: str, *, fast: bool) -> list[SourceDocument]:
        source_ids = [source.source_id for source in self.source_registry.list_for_context(ctx)]
        if not source_ids:
            return []
        if fast:
            source_ids = source_ids[:2]
        try:
            collected = await self.collector.collect_many(source_ids)
        except Exception:
            return []
        documents: list[SourceDocument] = []
        per_source_limit = 3 if fast else 5
        for source_id in source_ids:
            documents.extend(collected.get(source_id, [])[:per_source_limit])
        return documents

    def _filter_web_documents(
        self,
        documents: list[SourceDocument],
        topic: str,
        *,
        fast: bool,
    ) -> list[SourceDocument]:
        ranked: list[tuple[float, SourceDocument]] = []
        for document in documents:
            score = _topic_overlap(topic, document.content, document.title)
            if score <= 0:
                continue
            ranked.append((score, document))
        ranked.sort(key=lambda item: item[0], reverse=True)
        limit = 4 if fast else 8
        return [document for _, document in ranked[:limit]]

    @staticmethod
    def _web_document_to_candidate(document: SourceDocument) -> ExpansionCandidate:
        return ExpansionCandidate(
            title=document.title,
            extracted_text=(document.content or document.summary)[:1600],
            source_url=document.source_url,
            published_at=document.published_at,
        )

    @staticmethod
    def _dedupe_candidates(candidates: list[ExpansionCandidate]) -> list[ExpansionCandidate]:
        seen: set[tuple[str, str]] = set()
        deduped: list[ExpansionCandidate] = []
        for candidate in candidates:
            key = (candidate.source_url, candidate.title.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    @staticmethod
    def _should_run_cloud_review(scored, web_documents: list[SourceDocument]) -> bool:
        keep_count = sum(1 for item in scored if item.decision is ExpansionDecision.KEEP)
        maybe_count = sum(1 for item in scored if item.decision is ExpansionDecision.MAYBE)
        return bool(web_documents) and (keep_count == 0 or maybe_count > 0)

    async def _run_cloud_review(self, ctx: str, topic: str, scored) -> dict[str, object]:
        candidates = [item for item in scored if item.decision is not ExpansionDecision.DISCARD][:4]
        if not candidates:
            return self._empty_cloud_review()
        response = await litellm.acompletion(
            model=self.runtime.classifier_cloud_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Revise os candidatos de expansao e devolva um resumo curto em portugues. "
                        "Use somente as evidencias fornecidas."
                    ),
                },
                {
                    "role": "user",
                    "content": "\n\n".join(
                        [
                            f"TOPIC: {topic}",
                            *[
                                (
                                    f"TITLE: {item.candidate.title}\n"
                                    f"SOURCE: {item.candidate.source_url}\n"
                                    f"DECISION: {item.decision.value}\n"
                                    f"REASONING: {item.reasoning}\n"
                                    f"EXTRACT: {item.candidate.extracted_text[:600]}"
                                )
                                for item in candidates
                            ],
                        ]
                    ),
                },
            ],
            max_tokens=180,
        )
        summary = str(response.choices[0].message.content).strip()
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        if prompt_tokens == 0 and completion_tokens == 0:
            prompt_tokens = _estimate_tokens(
                "\n".join(item.candidate.extracted_text for item in candidates)
            )
            completion_tokens = _estimate_tokens(summary)
        cost_usd = round(
            ((prompt_tokens + completion_tokens) / 1000)
            * _CLOUD_COST_PER_1K.get(self.runtime.classifier_cloud_model, 0.001),
            6,
        )
        return {
            "summary": summary,
            "cost_usd": cost_usd,
            "budget_record": self.budget_usage_record(
                ctx=ctx,
                topic=topic,
                amount_usd=cost_usd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
        }

    def budget_usage_record(
        self,
        *,
        ctx: str,
        topic: str,
        amount_usd: float,
        prompt_tokens: int,
        completion_tokens: int,
    ):
        from prometheus.expansion.budget import BudgetUsageRecord

        return BudgetUsageRecord(
            execution_id=str(uuid.uuid4()),
            amount_usd=amount_usd,
            model=self.runtime.classifier_cloud_model,
            ctx=ctx,
            topic=topic,
            metadata={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "stage": "expand_cloud_review",
            },
        )

    @staticmethod
    def _merge_summaries(base: str, cloud_summary: str | None) -> str:
        if not cloud_summary:
            return base
        return f"{base} Cloud review: {cloud_summary}"

    @staticmethod
    def _cloud_reason_with_details(cloud_reason: str, cloud_summary: str | None) -> str:
        if not cloud_summary:
            return cloud_reason
        return f"{cloud_reason}; cloud_review=used"

    @staticmethod
    def _empty_cloud_review() -> dict[str, object]:
        return {"summary": None, "cost_usd": 0.0, "budget_record": None}


def _slugify(value: str) -> str:
    collapsed = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return collapsed.strip("-") or "expansion"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _excerpt(content: str) -> str:
    lines = [
        line.strip() for line in content.splitlines() if line.strip() and line.strip() != "---"
    ]
    return "\n".join(lines[:12])[:1600]


def _topic_overlap(topic: str, content: str, title: str) -> float:
    topic_tokens = set(_TOKEN_RE.findall(topic.lower()))
    if not topic_tokens:
        return 0.0
    haystack = f"{title}\n{content[:4000]}".lower()
    matched = sum(1 for token in topic_tokens if token in haystack)
    return matched / len(topic_tokens)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
