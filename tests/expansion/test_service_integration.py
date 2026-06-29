from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from axon.config.runtime import load_runtime_config
from axon.expansion.models import SourceDefinition, SourceFormat
from axon.expansion.registry import SourceRegistry
from axon.expansion.scoring import ExpansionDecision
from axon.expansion.service import ExpansionService
from axon.expansion.staging import load_draft
from axon.store.failure_store import FailureStore
from axon.store.outcome_store import OutcomeStore


class FakeTransport:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses

    async def fetch(self, url: str, source=None):
        _ = source
        from axon.expansion.models import SourceResponse

        return SourceResponse(
            url=url,
            status_code=200,
            text=self.responses[url],
            content_type="application/xml",
        )


def _scored_candidates(candidates, decision=ExpansionDecision.KEEP):
    return [
        SimpleNamespace(
            candidate=item,
            decision=decision,
            reasoning="deterministic test score",
            evidence_quotes=(),
            score=SimpleNamespace(
                relevance=0.9,
                novelty=0.8,
                actionability=0.8,
                evidence=0.9,
                weighted_total=0.86,
            ),
        )
        for item in candidates
    ]


def test_run_includes_registered_web_sources_in_staging(monkeypatch, tmp_path: Path) -> None:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("AXON_ENGINE", str(engine_root))
    monkeypatch.setenv("AXON_VAULT", str(vault_root))

    knowledge_root = vault_root / "knowledge"
    knowledge_root.mkdir(parents=True, exist_ok=True)
    (knowledge_root / "local.md").write_text(
        "# Vector search\ncreated: 2026-04-23\nLocal note about vector search filters.\n",
        encoding="utf-8",
    )

    registry = SourceRegistry(
        (
            SourceDefinition(
                source_id="knowledge-feed",
                name="Knowledge Feed",
                endpoint="https://knowledge.example.com/rss.xml",
                format=SourceFormat.RSS,
                allowed_contexts=("knowledge",),
            ),
        )
    )
    feed = """<?xml version="1.0"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>Vector search tuning</title>
          <link>https://knowledge.example.com/vector-search</link>
          <description><![CDATA[<p>Vector search tuning for filters and recall.</p>]]></description>
          <pubDate>Tue, 23 Apr 2026 15:30:00 +0000</pubDate>
        </item>
      </channel>
    </rss>"""
    service = ExpansionService(
        load_runtime_config(),
        source_registry=registry,
        collector_transport=FakeTransport({"https://knowledge.example.com/rss.xml": feed}),
    )

    staging_path = service.run(ctx="knowledge", topic="vector search", fast=True, allow_cloud=False)
    draft = load_draft(staging_path)

    assert any(
        source.source_url == "https://knowledge.example.com/vector-search"
        for source in draft.sources
    )


def test_run_records_budget_when_cloud_review_is_used(monkeypatch, tmp_path: Path) -> None:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("AXON_ENGINE", str(engine_root))
    monkeypatch.setenv("AXON_VAULT", str(vault_root))

    knowledge_root = vault_root / "knowledge"
    knowledge_root.mkdir(parents=True, exist_ok=True)
    (knowledge_root / "local.md").write_text(
        "# Search\ncreated: 2026-04-23\nVector tuning note.\n",
        encoding="utf-8",
    )

    registry = SourceRegistry(
        (
            SourceDefinition(
                source_id="knowledge-feed",
                name="Knowledge Feed",
                endpoint="https://knowledge.example.com/rss.xml",
                format=SourceFormat.RSS,
                allowed_contexts=("knowledge",),
            ),
        )
    )
    feed = """<?xml version="1.0"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>Vector search review</title>
          <link>https://knowledge.example.com/vector-review</link>
          <description><![CDATA[<p>Vector search review with tradeoffs.</p>]]></description>
          <pubDate>Tue, 23 Apr 2026 15:30:00 +0000</pubDate>
        </item>
      </channel>
    </rss>"""

    async def fake_acompletion(*, model: str, messages: list[dict], max_tokens: int):
        _ = (messages, max_tokens)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Cloud summary for ambiguous candidates.")
                )
            ],
            usage=SimpleNamespace(prompt_tokens=500, completion_tokens=100),
        )

    monkeypatch.setattr("axon.expansion.service.litellm.acompletion", fake_acompletion)
    monkeypatch.setattr(
        "axon.expansion.service.score_candidates",
        lambda candidates, topic: [
            SimpleNamespace(
                candidate=candidates[0],
                decision=ExpansionDecision.MAYBE,
                reasoning="ambiguous",
                evidence_quotes=(),
                score=SimpleNamespace(
                    relevance=0.5,
                    novelty=0.5,
                    actionability=0.5,
                    evidence=0.5,
                    weighted_total=0.51,
                ),
            ),
            *[
                SimpleNamespace(
                    candidate=item,
                    decision=ExpansionDecision.DISCARD,
                    reasoning="discard",
                    evidence_quotes=(),
                    score=SimpleNamespace(
                        relevance=0.1,
                        novelty=0.1,
                        actionability=0.1,
                        evidence=0.1,
                        weighted_total=0.1,
                    ),
                )
                for item in candidates[1:]
            ],
        ],
    )

    service = ExpansionService(
        load_runtime_config(),
        source_registry=registry,
        collector_transport=FakeTransport({"https://knowledge.example.com/rss.xml": feed}),
    )

    staging_path = service.run(ctx="knowledge", topic="vector search", fast=True, allow_cloud=True)
    draft = load_draft(staging_path)
    budget_file = load_runtime_config().expansion.paths.monthly_budget_file()
    budget_payload = json.loads(budget_file.read_text(encoding="utf-8"))

    assert draft.cloud_mode == "cloud_allowed"
    assert "cloud_review=used" in draft.cloud_reason
    assert budget_payload["spent_usd"] > 0
    assert budget_payload["entries"][0]["metadata"]["stage"] == "expand_cloud_review"


def test_run_approve_reject_persist_outcomes(monkeypatch, tmp_path: Path) -> None:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("AXON_ENGINE", str(engine_root))
    monkeypatch.setenv("AXON_VAULT", str(vault_root))

    knowledge_root = vault_root / "knowledge"
    knowledge_root.mkdir(parents=True, exist_ok=True)
    (knowledge_root / "vector.md").write_text(
        "# Vector Search\ncreated: 2026-04-23\nVector tuning guidance.\n",
        encoding="utf-8",
    )
    (knowledge_root / "ranking.md").write_text(
        "# Ranking Signals\ncreated: 2026-04-24\nRanking note for vector search.\n",
        encoding="utf-8",
    )

    service = ExpansionService(load_runtime_config())
    monkeypatch.setattr(
        "axon.expansion.service.score_candidates",
        lambda candidates, topic: _scored_candidates(candidates),
    )

    async def fake_reindex_publish_path(publish_path: Path, ctx: str) -> None:
        _ = (publish_path, ctx)

    monkeypatch.setattr(service, "_reindex_publish_path", fake_reindex_publish_path)

    staged_path = service.run(ctx="knowledge", topic="vector search", fast=True, allow_cloud=False)
    publish_path, reindex_status = service.approve(staged_path)
    rejected_stage = service.run(
        ctx="knowledge", topic="ranking notes", fast=True, allow_cloud=False
    )
    rejected_path = service.reject(rejected_stage)

    store = OutcomeStore(engine_root / "data" / "outcomes.db")
    asyncio.run(store.init())
    outcomes = asyncio.run(store.get_outcomes_for_context("axon", "knowledge", limit=10))
    asyncio.run(store.close())

    assert publish_path.exists()
    assert reindex_status in {"reindex_ok", "reindex_skipped"}
    assert rejected_path.exists()
    assert [item.outcome for item in outcomes[:3]] == [
        "expansion_rejected",
        "expansion_run_staged",
        "expansion_approved",
    ]
    assert [item.outcome for item in outcomes] == [
        "expansion_rejected",
        "expansion_run_staged",
        "expansion_approved",
        "expansion_run_staged",
    ]


def test_approve_reindex_failures_are_persisted_for_repeated_failure_queries(
    monkeypatch, tmp_path: Path
) -> None:
    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("AXON_ENGINE", str(engine_root))
    monkeypatch.setenv("AXON_VAULT", str(vault_root))

    knowledge_root = vault_root / "knowledge"
    knowledge_root.mkdir(parents=True, exist_ok=True)
    (knowledge_root / "first.md").write_text(
        "# Vector Search\ncreated: 2026-04-23\nVector tuning guidance.\n",
        encoding="utf-8",
    )
    (knowledge_root / "second.md").write_text(
        "# Hybrid Search\ncreated: 2026-04-24\nHybrid search guidance.\n",
        encoding="utf-8",
    )

    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        dsn = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        monkeypatch.setenv("AXON_PG_URL", dsn)

        service = ExpansionService(load_runtime_config())
        monkeypatch.setattr(
            "axon.expansion.service.score_candidates",
            lambda candidates, topic: _scored_candidates(candidates),
        )

        async def failing_reindex_publish_path(publish_path: Path, ctx: str) -> None:
            _ = (publish_path, ctx)
            raise RuntimeError("qdrant unavailable")

        monkeypatch.setattr(service, "_reindex_publish_path", failing_reindex_publish_path)

        first_stage = service.run(
            ctx="knowledge", topic="vector search", fast=True, allow_cloud=False
        )
        second_stage = service.run(
            ctx="knowledge", topic="hybrid search", fast=True, allow_cloud=False
        )

        _, first_status = service.approve(first_stage)
        _, second_status = service.approve(second_stage)

        store = FailureStore(dsn=dsn)
        failures = asyncio.run(store.get_recent_failures("axon", limit=10))
        repeated = asyncio.run(store.get_repeated_failures("axon", min_occurrences=2, limit=10))

    assert first_status == "reindex_skipped"
    assert second_status == "reindex_skipped"
    assert len(failures) == 2
    assert all(item.operation == "expansion_approve_reindex" for item in failures)
    assert repeated == [("publish reindex failed", 2)]
