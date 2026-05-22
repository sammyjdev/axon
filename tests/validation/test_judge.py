"""Tests for the LLM decision judge (T5.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from axon.core.decision import Decision
from axon.validation import judge
from axon.validation.prompts import build_judge_prompt


def _decision(**overrides: Any) -> Decision:
    base: dict[str, Any] = dict(
        id="dec-001",
        timestamp=datetime(2026, 5, 1, tzinfo=UTC),
        agent="claude-code",
        repo="axon",
        summary="drop neo4j from the graph backend",
    )
    base.update(overrides)
    return Decision(**base)


def test_build_judge_prompt_includes_decision_fields() -> None:
    prompt = build_judge_prompt(_decision(symbols=["pkg.Mod"]))
    assert "drop neo4j from the graph backend" in prompt
    assert "pkg.Mod" in prompt
    assert "0.0-5.0" in prompt


async def test_score_decision_parses_number(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_complete(task: Any, messages: Any) -> str:
        return "The score is 3.5"

    monkeypatch.setattr(judge, "complete", fake_complete)
    assert await judge.score_decision(_decision()) == 3.5


async def test_score_decision_clamps_to_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete(task: Any, messages: Any) -> str:
        return "9.9"

    monkeypatch.setattr(judge, "complete", fake_complete)
    assert await judge.score_decision(_decision()) == 5.0


async def test_score_decision_none_when_provider_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(task: Any, messages: Any) -> str:
        raise RuntimeError("no API key")

    monkeypatch.setattr(judge, "complete", boom)
    assert await judge.score_decision(_decision()) is None


async def test_score_decision_none_when_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete(task: Any, messages: Any) -> str:
        return "no number here"

    monkeypatch.setattr(judge, "complete", fake_complete)
    assert await judge.score_decision(_decision()) is None
