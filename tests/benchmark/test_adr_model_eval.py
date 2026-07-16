from __future__ import annotations

import json

from axon.benchmark.model_eval import ADREvalCase, evaluate_adr_model

_ADR_CASE = ADREvalCase(
    commit_message="arch: extract call graph via tree-sitter instead of regex",
    diff_summary="src/axon/code/graph.py | 120 +++---",
    expected="adr",
    key_terms=("tree-sitter", "call graph"),
)

_NULL_CASE = ADREvalCase(
    commit_message="style: simplify dashboard typography",
    diff_summary="src/axon/http/promotions_dashboard.py | 40 ++--",
    expected="null",
    key_terms=(),
)


def _chat_returning(reply: str):
    def chat(model: str, prompt: str) -> str:
        return reply

    return chat


def _passed(result, name: str) -> list[bool]:
    return [c.passed for c in result.checks if c.name == name]


def test_null_expected_passes_on_null_reply() -> None:
    result = evaluate_adr_model(
        "fake", [_NULL_CASE], chat=_chat_returning("null")
    )
    assert _passed(result, "verdict_match") == [True]


def test_null_expected_fails_when_model_invents_adr() -> None:
    reply = json.dumps(
        {"title": "t", "context": "c", "decision": "d", "rationale": "r"}
    )
    result = evaluate_adr_model(
        "fake", [_NULL_CASE], chat=_chat_returning(reply)
    )
    assert _passed(result, "verdict_match") == [False]


def test_adr_expected_checks_json_and_key_terms() -> None:
    reply = json.dumps(
        {
            "title": "call graph via tree-sitter",
            "context": "graph extraction in src/axon/code/graph.py",
            "decision": "replace regex extraction with tree-sitter call graph",
            "rationale": "regex misses nested scopes; tree-sitter is exact",
        }
    )
    result = evaluate_adr_model(
        "fake", [_ADR_CASE], chat=_chat_returning(reply)
    )
    assert _passed(result, "verdict_match") == [True]
    assert _passed(result, "json_valid") == [True]
    assert _passed(result, "key_terms_present") == [True]


def test_adr_expected_flags_missing_key_terms() -> None:
    reply = json.dumps(
        {
            "title": "t",
            "context": "generic context",
            "decision": "generic decision",
            "rationale": "generic rationale",
        }
    )
    result = evaluate_adr_model(
        "fake", [_ADR_CASE], chat=_chat_returning(reply)
    )
    assert _passed(result, "key_terms_present") == [False]


def test_adr_expected_flags_unparseable_reply() -> None:
    result = evaluate_adr_model(
        "fake", [_ADR_CASE], chat=_chat_returning("Sure! Here is the JSON:")
    )
    assert _passed(result, "verdict_match") == [False]
    assert _passed(result, "json_valid") == [False]
