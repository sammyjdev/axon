"""LLM-judge — scores architectural decisions (T5.1).

Reuses the router (D2 model map + litellm + budget guardrails) rather than
calling the Anthropic API directly. Degrades gracefully: with no API key or
any provider failure, the score is ``None`` and a warning is logged.
"""

from __future__ import annotations

import logging
import re

from axon.core.decision import Decision
from axon.router.engine import TaskRequest, complete
from axon.validation.prompts import build_judge_prompt

logger = logging.getLogger(__name__)

_SCORE_RE = re.compile(r"-?\d+(?:\.\d+)?")
_MIN_SCORE = 0.0
_MAX_SCORE = 5.0


def _parse_score(raw: str) -> float | None:
    match = _SCORE_RE.search(raw)
    if match is None:
        return None
    return max(_MIN_SCORE, min(_MAX_SCORE, float(match.group())))


async def score_decision(decision: Decision, context: str = "") -> float | None:
    """Score a Decision from 0.0 to 5.0 via the router's LLM.

    Returns ``None`` when the judge is unavailable or its reply is unparseable.
    """
    prompt = build_judge_prompt(decision, context)
    try:
        raw = await complete(TaskRequest(content=prompt), [])
    except Exception as exc:  # provider/budget/policy failure — never fatal
        logger.warning("decision judge unavailable, score skipped: %s", exc)
        return None
    score = _parse_score(raw or "")
    if score is None:
        logger.warning("decision judge returned an unparseable score: %r", raw)
    return score
