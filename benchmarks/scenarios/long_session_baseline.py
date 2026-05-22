"""Baseline scenario: a 20-turn coding session WITHOUT AXON.

The whole project context is re-supplied every turn and grows as decisions
accumulate. See benchmarks/model.py for the modelling assumptions.
"""

from __future__ import annotations

from benchmarks.model import DEFAULT_SESSION, session_total


def run() -> int:
    """Print and return total input tokens for the baseline session."""
    p = DEFAULT_SESSION
    total = session_total(p, mode="baseline")
    print(
        f"[baseline] {p.turns}-turn session, no AXON: {total} input tokens "
        f"(context re-supplied each turn, +{p.growth_per_turn}/turn growth)"
    )
    return total


if __name__ == "__main__":
    run()
