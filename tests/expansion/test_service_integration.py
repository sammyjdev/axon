from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from prometheus.config.runtime import load_runtime_config
from prometheus.expansion.models import SourceDefinition, SourceFormat
from prometheus.expansion.registry import SourceRegistry
from prometheus.expansion.scoring import ExpansionDecision
from prometheus.expansion.service import ExpansionService
from prometheus.expansion.staging import load_draft


class FakeTransport:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses

    async def fetch(self, url: str, source=None):
        _ = source
        from prometheus.expansion.models import SourceResponse

        return SourceResponse(
            url=url,
            status_code=200,
            text=self.responses[url],
            content_type="application/xml",
        )


def test_run_includes_registered_web_sources_in_staging(monkeypatch, tmp_path: Path) -> None:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(engine_root))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(vault_root))

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

    assert any(source.source_url == "https://knowledge.example.com/vector-search" for source in draft.sources)


def test_run_records_budget_when_cloud_review_is_used(monkeypatch, tmp_path: Path) -> None:
    engine_root = tmp_path / "engine"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(engine_root))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(vault_root))

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
            choices=[SimpleNamespace(message=SimpleNamespace(content="Cloud summary for ambiguous candidates."))],
            usage=SimpleNamespace(prompt_tokens=500, completion_tokens=100),
        )

    monkeypatch.setattr("prometheus.expansion.service.litellm.acompletion", fake_acompletion)
    monkeypatch.setattr(
        "prometheus.expansion.service.score_candidates",
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
