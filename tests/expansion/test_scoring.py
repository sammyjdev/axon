from __future__ import annotations

from prometheus.expansion.scoring import (
    DEFAULT_LOCAL_MODEL,
    ExpansionCandidate,
    ExpansionDecision,
    score_candidate,
    score_candidates,
)


def test_score_candidate_uses_local_slm_by_default(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, host: str) -> None:
            captured["host"] = host

        def chat(self, *, model: str, format: str, messages, options):
            captured["model"] = model
            captured["format"] = format
            captured["messages"] = messages
            captured["options"] = options
            return {
                "message": {
                    "content": """
                    {
                      "relevance": 0.91,
                      "novelty": 0.63,
                      "actionability": 0.88,
                      "evidence": 0.84,
                      "decision": "keep",
                      "reasoning": "Muito alinhado ao topico e com exemplo claro.",
                      "evidence_quotes": [
                        "Run `pb index` after approving the note.",
                        "Version 2.1 introduced staged review before publishing."
                      ]
                    }
                    """
                }
            }

    monkeypatch.setattr("prometheus.expansion.scoring.ollama.Client", FakeClient)

    candidate = ExpansionCandidate(
        title="Staged review for expansion",
        source_url="https://example.com/release-notes",
        published_at="2026-04-20",
        extracted_text=(
            "Version 2.1 introduced staged review before publishing.\n"
            "Run `pb index` after approving the note.\n"
            "This example shows how to configure the flow safely."
        ),
    )

    result = score_candidate(candidate, "staged review for knowledge expansion")

    assert result.source == "local_slm"
    assert result.model == DEFAULT_LOCAL_MODEL
    assert result.decision == ExpansionDecision.KEEP
    assert result.score.relevance == 0.91
    assert result.score.evidence == 0.84
    assert "Evidencia:" in result.reasoning
    assert captured["model"] == DEFAULT_LOCAL_MODEL
    assert captured["format"] == "json"
    assert captured["options"] == {"temperature": 0}


def test_score_candidate_falls_back_when_model_invents_evidence(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, host: str) -> None:
            self.host = host

        def chat(self, *, model: str, format: str, messages, options):
            _ = (model, format, messages, options)
            return {
                "message": {
                    "content": """
                    {
                      "relevance": 0.99,
                      "novelty": 0.80,
                      "actionability": 0.70,
                      "evidence": 0.90,
                      "decision": "keep",
                      "reasoning": "Tem boas evidencias.",
                      "evidence_quotes": ["Texto que nao existe no documento extraido."]
                    }
                    """
                }
            }

    monkeypatch.setattr("prometheus.expansion.scoring.ollama.Client", FakeClient)

    candidate = ExpansionCandidate(
        title="Minimal notes",
        source_url="https://example.com/minimal",
        published_at="2026-04-20",
        extracted_text="Plain operational note without the claimed quote.",
    )

    result = score_candidate(candidate, "operational note")

    assert result.source == "heuristic"
    assert result.model == "heuristic"
    assert result.decision in {ExpansionDecision.MAYBE, ExpansionDecision.DISCARD}


def test_score_candidate_discards_unrelated_content_with_heuristics(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, host: str) -> None:
            self.host = host

        def chat(self, *, model: str, format: str, messages, options):
            raise RuntimeError("ollama offline")

    monkeypatch.setattr("prometheus.expansion.scoring.ollama.Client", FakeClient)

    candidate = ExpansionCandidate(
        title="Gardening notes",
        source_url="https://example.com/garden",
        extracted_text=(
            "Water tomatoes in the early morning.\n"
            "Add compost once a month.\n"
            "Observe leaf color before pruning."
        ),
    )

    result = score_candidate(candidate, "java stream pipeline optimization")

    assert result.source == "heuristic"
    assert result.decision == ExpansionDecision.DISCARD
    assert result.score.relevance == 0.0


def test_score_candidate_returns_maybe_for_relevant_but_thin_content(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, host: str) -> None:
            self.host = host

        def chat(self, *, model: str, format: str, messages, options):
            raise RuntimeError("use fallback")

    monkeypatch.setattr("prometheus.expansion.scoring.ollama.Client", FakeClient)

    candidate = ExpansionCandidate(
        title="Java Streams gotcha",
        source_url="https://example.com/streams",
        extracted_text=(
            "Java Streams can hide allocation costs.\n"
            "Watch collector choices during profiling."
        ),
    )

    result = score_candidate(candidate, "java streams profiling")

    assert result.source == "heuristic"
    assert result.decision == ExpansionDecision.MAYBE
    assert result.score.relevance >= 0.5


def test_score_candidates_preserves_order(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, host: str) -> None:
            self.host = host

        def chat(self, *, model: str, format: str, messages, options):
            raise RuntimeError("fallback")

    monkeypatch.setattr("prometheus.expansion.scoring.ollama.Client", FakeClient)

    candidates = [
        ExpansionCandidate(
            title="First",
            source_url="https://example.com/1",
            extracted_text="Example command for java profiling and flamegraph analysis.",
        ),
        ExpansionCandidate(
            title="Second",
            source_url="https://example.com/2",
            extracted_text="Completely unrelated travel checklist.",
        ),
    ]

    results = score_candidates(candidates, "java profiling command")

    assert [result.candidate.title for result in results] == ["First", "Second"]
