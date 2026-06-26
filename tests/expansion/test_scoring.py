from __future__ import annotations

import logging
from types import SimpleNamespace

from axon.expansion.scoring import (
    ExpansionCandidate,
    ExpansionDecision,
    score_candidate,
    score_candidates,
)


def _runtime(**over):
    base = {
        "scoring_model": "groq/openai/gpt-oss-120b",
        "ollama_local_host": "http://desktop:11434",
        "scoring_num_ctx": 8192,
        "provider_ollama_enabled": False,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _resp(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _raise(**kw):
    raise RuntimeError("provider offline")


def _patch(monkeypatch, *, runtime, completion):
    monkeypatch.setattr("axon.expansion.scoring._RUNTIME", runtime)
    monkeypatch.setattr("axon.expansion.scoring.litellm.completion", completion)


def test_score_candidate_uses_slm_when_enabled(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_completion(**kw):
        captured.update(kw)
        return _resp(
            '{"relevance":0.91,"novelty":0.63,"actionability":0.88,"evidence":0.84,'
            '"decision":"keep","reasoning":"Alinhado ao topico.",'
            '"evidence_quotes":["Run `pb index` after approving the note."]}'
        )

    _patch(monkeypatch, runtime=_runtime(), completion=fake_completion)

    candidate = ExpansionCandidate(
        title="Staged review for expansion",
        source_url="https://example.com/release-notes",
        extracted_text=(
            "Version 2.1 introduced staged review before publishing.\n"
            "Run `pb index` after approving the note.\n"
            "This example shows how to configure the flow safely."
        ),
    )

    result = score_candidate(candidate, "staged review for knowledge expansion")

    assert result.source == "local_slm"
    assert result.model == "groq/openai/gpt-oss-120b"
    assert result.decision == ExpansionDecision.KEEP
    assert result.score.relevance == 0.91
    assert captured["model"] == "groq/openai/gpt-oss-120b"
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["temperature"] == 0
    assert "api_base" not in captured  # cloud model carries no ollama host


def test_score_candidate_falls_back_when_model_invents_evidence(monkeypatch) -> None:
    def fake_completion(**kw):
        return _resp(
            '{"relevance":0.99,"novelty":0.80,"actionability":0.70,"evidence":0.90,'
            '"decision":"keep","reasoning":"Tem evidencias.",'
            '"evidence_quotes":["Texto que nao existe no documento extraido."]}'
        )

    _patch(monkeypatch, runtime=_runtime(), completion=fake_completion)

    candidate = ExpansionCandidate(
        title="Minimal notes",
        source_url="https://example.com/minimal",
        extracted_text="Plain operational note without the claimed quote.",
    )

    result = score_candidate(candidate, "operational note")

    assert result.source == "heuristic"
    assert result.decision in {ExpansionDecision.MAYBE, ExpansionDecision.DISCARD}


def test_score_candidate_skips_slm_when_ollama_opted_out(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_completion(**kw):
        calls["n"] += 1
        raise AssertionError("must not be called when ollama is opted out")

    _patch(
        monkeypatch,
        runtime=_runtime(scoring_model="ollama/gemma4:e4b", provider_ollama_enabled=False),
        completion=fake_completion,
    )

    candidate = ExpansionCandidate(
        title="Java profiling",
        source_url="https://example.com/jp",
        extracted_text="Use async-profiler to capture a flamegraph of JVM CPU time.",
    )

    result = score_candidate(candidate, "java profiling")

    assert calls["n"] == 0
    assert result.source == "heuristic"


def test_score_candidate_logs_on_slm_failure(monkeypatch, caplog) -> None:
    def boom(**kw):
        raise RuntimeError("provider offline")

    _patch(monkeypatch, runtime=_runtime(), completion=boom)

    candidate = ExpansionCandidate(
        title="Gardening notes",
        source_url="https://example.com/garden",
        extracted_text="Water tomatoes in the early morning.\nAdd compost once a month.",
    )

    with caplog.at_level(logging.WARNING):
        result = score_candidate(candidate, "java stream pipeline optimization")

    assert result.source == "heuristic"
    assert any("heuristic" in rec.message for rec in caplog.records)


def test_score_candidate_discards_unrelated_content_with_heuristics(monkeypatch) -> None:
    _patch(monkeypatch, runtime=_runtime(), completion=_raise)

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


def test_score_candidates_preserves_order(monkeypatch) -> None:
    _patch(monkeypatch, runtime=_runtime(), completion=_raise)

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
