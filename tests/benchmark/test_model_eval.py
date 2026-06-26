from __future__ import annotations

from axon.benchmark.model_eval import (
    CompressorEvalCase,
    ScoringEvalCase,
    evaluate_compressor_model,
    evaluate_scoring_model,
)
from axon.expansion.scoring import ExpansionCandidate, ExpansionDecision


def _check(result, name):
    return next(c for c in result.checks if c.name == name)


def _scoring_case() -> ScoringEvalCase:
    return ScoringEvalCase(
        candidate=ExpansionCandidate(
            title="Java flamegraph profiling",
            extracted_text=(
                "Use async-profiler to capture a flamegraph of JVM CPU time "
                "and identify the hottest call paths during load."
            ),
            source_url="https://example.com/java-profiling",
        ),
        topic="java profiling",
        gold_decision=ExpansionDecision.KEEP,
    )


def test_scoring_eval_reflects_json_validity() -> None:
    case = _scoring_case()
    valid = (
        '{"relevance":0.9,"novelty":0.5,"actionability":0.7,"evidence":0.8,'
        '"decision":"keep","reasoning":"ok",'
        '"evidence_quotes":["Use async-profiler to capture a flamegraph"]}'
    )

    ok = evaluate_scoring_model("fake", [case], chat=lambda model, prompt: valid)
    assert _check(ok, "json_valid").passed

    bad = evaluate_scoring_model("fake", [case], chat=lambda model, prompt: "sorry, no JSON here")
    assert not _check(bad, "json_valid").passed


def test_scoring_eval_flags_hallucinated_quotes() -> None:
    case = _scoring_case()
    base = (
        '{"relevance":0.9,"novelty":0.5,"actionability":0.7,"evidence":0.8,'
        '"decision":"keep","reasoning":"ok",'
    )
    grounded = base + '"evidence_quotes":["Use async-profiler to capture a flamegraph"]}'
    invented = base + '"evidence_quotes":["This sentence never appeared in the source material"]}'

    ok = evaluate_scoring_model("fake", [case], chat=lambda model, prompt: grounded)
    assert _check(ok, "grounded").passed

    bad = evaluate_scoring_model("fake", [case], chat=lambda model, prompt: invented)
    assert not _check(bad, "grounded").passed


def test_scoring_eval_matches_gold_decision() -> None:
    case = _scoring_case()  # gold_decision = KEEP
    quotes = '"evidence_quotes":["Use async-profiler to capture a flamegraph"]}'
    keep = (
        '{"relevance":0.9,"novelty":0.5,"actionability":0.7,"evidence":0.8,'
        '"decision":"keep","reasoning":"ok",' + quotes
    )
    discard = (
        '{"relevance":0.1,"novelty":0.1,"actionability":0.1,"evidence":0.1,'
        '"decision":"discard","reasoning":"ok",' + quotes
    )

    ok = evaluate_scoring_model("fake", [case], chat=lambda model, prompt: keep)
    assert _check(ok, "decision_match").passed

    bad = evaluate_scoring_model("fake", [case], chat=lambda model, prompt: discard)
    assert not _check(bad, "decision_match").passed


def _compressor_case() -> CompressorEvalCase:
    return CompressorEvalCase(
        text=(
            "The function calculate_total(items) must preserve the TAX_RATE "
            "constant and always call audit_log() before returning."
        ),
        required_symbols=("calculate_total", "TAX_RATE", "audit_log"),
    )


def test_compressor_eval_flags_dropped_symbols() -> None:
    case = _compressor_case()
    kept = "calculate_total preserves TAX_RATE, calls audit_log before return"
    dropped = "the function totals the items applying tax then logs"

    ok = evaluate_compressor_model("fake", [case], compress=lambda model, text: kept)
    assert _check(ok, "symbols_preserved").passed

    bad = evaluate_compressor_model("fake", [case], compress=lambda model, text: dropped)
    assert not _check(bad, "symbols_preserved").passed


def test_compressor_eval_measures_compression() -> None:
    case = _compressor_case()
    shorter = "calculate_total TAX_RATE audit_log kept"
    longer = case.text + " padding words added repeatedly" * 6

    ok = evaluate_compressor_model("fake", [case], compress=lambda model, text: shorter)
    assert _check(ok, "compressed").passed

    bad = evaluate_compressor_model("fake", [case], compress=lambda model, text: longer)
    assert not _check(bad, "compressed").passed


def test_eval_routes_model_and_records_latency() -> None:
    case = _scoring_case()
    seen: dict[str, str] = {}

    def chat(model: str, prompt: str) -> str:
        seen["model"] = model
        return (
            '{"relevance":0.9,"novelty":0.5,"actionability":0.7,"evidence":0.8,'
            '"decision":"keep","reasoning":"ok",'
            '"evidence_quotes":["Use async-profiler to capture a flamegraph"]}'
        )

    result = evaluate_scoring_model("qwen3:4b", [case, case], chat=chat)

    assert seen["model"] == "qwen3:4b"
    assert result.name == "qwen3:4b"
    assert result.suite == "model_eval.scoring"
    assert result.duration_ms >= 0.0
    assert result.score == 1.0  # all checks pass for both cases
