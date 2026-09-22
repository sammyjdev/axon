"""Pinned questions and operating points for the TypeSafe benchmark pilot."""

from __future__ import annotations

from axon.recall.strategy import _NEAR_DUP_THRESHOLD, _SCOPE_SIM_THRESHOLD

ENDPOINT = "https://api.typesafe.ai/v1/systemone"

# Pinned on purpose: the alias `jev-latest` moves and would silently change answers
# under calibrated thresholds.
MODEL = "jev-1.13.0"

# Pinned on purpose: EmbedderEngine()'s default is resolved at runtime; the pilot
# records which embedder produced every cosine it reports.
EMBEDDER_MODEL = "bge-m3"

PRICE_PER_MTOK_INPUT = 0.042  # output tokens are free

# Re-exported under public names. Imported rather than copied so a change in
# production is a change in the pilot.
SCOPE_SIM_THRESHOLD = _SCOPE_SIM_THRESHOLD
NEAR_DUP_THRESHOLD = _NEAR_DUP_THRESHOLD

NOUL_PRIMARY_THRESHOLD = 0.5
NOUL_UNCERTAIN_LOW = 0.30
NOUL_UNCERTAIN_HIGH = 0.70


def supersession_question(older_summary: str, newer_summary: str) -> dict:
    """One noul: does the newer decision revise the older one?"""
    return {
        "type": "noul",
        "instructions": {
            "older_decision": older_summary,
            "newer_decision": newer_summary,
            "question": "Does `newer_decision` revise, reverse, or replace `older_decision`?",
        },
        "criteria": {
            "true": (
                "The newer decision changes what the older one established: it "
                "reverses it, replaces the chosen approach, or retires what it "
                "introduced."
            ),
            "false": (
                "The newer decision is additive work in the same area, or it "
                "implements, extends, fixes, or reports on the older one without "
                "changing it."
            ),
        },
    }


JUDGE_SINGLE_LEVELS = [
    "No decision is stated: the text only names an area touched or restates a task.",
    "A decision is stated, but neither what changed nor why is present.",
    "The decision and what changed are stated; no rationale and no scope.",
    "Decision, change and a rationale are present; scope or risk is left implicit.",
    "Decision, change, rationale and affected scope are present and consistent.",
    "All of the above, plus the trade-off considered or the risk accepted is explicit.",
]


def judge_single_question() -> dict:
    return {
        "type": "score",
        "instructions": (
            "Score this architectural decision record on clarity, completeness, "
            "alignment with the declared scope, and risk awareness."
        ),
        "criteria": list(JUDGE_SINGLE_LEVELS),
    }


JUDGE_COMPOSITE_LEVELS: dict[str, list[str]] = {
    "clarity": [
        "The summary does not state what was decided.",
        "What was decided is stated but ambiguous, or mixed with unrelated text.",
        "What was decided is stated in one unambiguous sentence.",
    ],
    "completeness": [
        "Only the decision is named.",
        "The decision and what changed are both present.",
        "Decision, change and rationale are all present.",
    ],
    "alignment": [
        "The declared files and symbols have no visible relation to the summary.",
        "The declared scope is plausible but only partially covers the summary.",
        "The declared files and symbols match what the summary says was changed.",
    ],
    "risk": [
        "No risk, trade-off or consequence is mentioned.",
        "A consequence is implied but never stated.",
        "A trade-off or an accepted risk is stated explicitly.",
    ],
}

JUDGE_COMPOSITE_WEIGHTS: dict[str, float] = {
    key: 0.25 for key in JUDGE_COMPOSITE_LEVELS
}


def judge_composite_questions() -> dict[str, dict]:
    """Four score questions, asked together over the same state."""
    return {
        name: {
            "type": "score",
            "instructions": name,
            "criteria": list(levels),
        }
        for name, levels in JUDGE_COMPOSITE_LEVELS.items()
    }


def compose_judge_score(scores: dict[str, float]) -> float:
    """Map per-dimension scores onto the 0.0-5.0 scale the current judge returns."""
    missing = set(JUDGE_COMPOSITE_LEVELS) - set(scores)
    if missing:
        raise ValueError(f"missing judge score: {sorted(missing)[0]}")
    return sum(
        JUDGE_COMPOSITE_WEIGHTS[name] * scores[name] / (len(levels) - 1)
        for name, levels in JUDGE_COMPOSITE_LEVELS.items()
    ) * 5.0
