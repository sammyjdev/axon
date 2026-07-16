"""Regression pin for the ADR classifier LLM contract (ADR-grid 2026-07-15).

``max_tokens=400`` truncated reasoning models into unparseable JSON
(gpt-oss-120b: 0/18 valid at 400, 18/18 at 2000, k=3 benchmark). The
contract is 2000 — do not lower it without re-running the grid.
"""

from __future__ import annotations

import pytest

from axon.adr import inference


@pytest.mark.asyncio
async def test_call_llm_requests_2000_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        class _Msg:
            content = "null"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    result = await inference._call_llm("chore: msg", "diff")
    assert result == "null"
    assert captured["max_tokens"] == 2000
