"""RETIRED (2026-07-02): this deterministic projection (the "52.3%" figure) is
superseded by the measured multi-turn harness in gnomon-eval
(`gnomon session -c config/axon-session.toml`; see gnomon-eval ADR-0010).
Kept for provenance: the projection-vs-measurement delta is part of the
published story. Do not cite this model's output as a measurement.

Deterministic token-cost model for an AXON benchmark session.

This is an explicit MODEL, not an instrumented capture. A session is `turns`
turns long. `baseline` mode re-supplies the whole (growing) project context
every turn; `axon` mode retrieves the project context once, then issues one
fixed-budget recall per turn. Constants are session assumptions, not tuned to
hit a target.
"""

from __future__ import annotations

from dataclasses import dataclass

MODES = ("baseline", "axon")


@dataclass(frozen=True)
class SessionParams:
    """Assumptions for a modelled coding session."""

    turns: int
    base_context: int
    growth_per_turn: int
    recall_budget: int

    def __post_init__(self) -> None:
        if self.turns < 1:
            raise ValueError(f"turns must be >= 1, got {self.turns}")
        if min(self.base_context, self.growth_per_turn, self.recall_budget) < 0:
            raise ValueError("token counts must be non-negative")


def turn_costs(params: SessionParams, *, mode: str) -> list[int]:
    """Input-token cost of each turn, 1-indexed, for the given mode."""
    if mode == "baseline":
        return [
            params.base_context + params.growth_per_turn * k
            for k in range(params.turns)
        ]
    if mode == "axon":
        costs = [params.recall_budget] * params.turns
        costs[0] += params.base_context
        return costs
    raise ValueError(f"unknown mode: {mode!r} (expected one of {MODES})")


def session_total(params: SessionParams, *, mode: str) -> int:
    """Total input tokens across the whole session."""
    return sum(turn_costs(params, mode=mode))


def savings(params: SessionParams) -> float:
    """Fraction of baseline tokens removed by AXON.

    Normally 0.0-1.0, but can be negative for very short sessions where the
    fixed recall budget has not yet paid off against the growing baseline.
    """
    base = session_total(params, mode="baseline")
    axon = session_total(params, mode="axon")
    return 1.0 - axon / base


# Default session assumptions, shared by both scenario runners so the
# baseline/axon comparison uses identical inputs.
DEFAULT_SESSION = SessionParams(
    turns=20, base_context=1500, growth_per_turn=300, recall_budget=2000
)
