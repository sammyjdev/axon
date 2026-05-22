"""AXON scenario: the same 20-turn session WITH on-demand recall.

Project context is retrieved once; each turn issues one fixed-budget recall.
See benchmarks/model.py for the modelling assumptions.
"""

from __future__ import annotations

from benchmarks.model import DEFAULT_SESSION, savings, session_total


def run() -> dict[str, float | int]:
    """Print and return axon/baseline totals and the savings fraction."""
    p = DEFAULT_SESSION
    baseline_total = session_total(p, mode="baseline")
    axon_total = session_total(p, mode="axon")
    frac = savings(p)
    print(
        f"[axon] {p.turns}-turn session, with AXON: {axon_total} input tokens "
        f"(recall budget {p.recall_budget}/turn)"
    )
    print(f"[axon] savings vs baseline ({baseline_total} tokens): {frac:.1%}")
    return {
        "baseline_total": baseline_total,
        "axon_total": axon_total,
        "savings": frac,
    }


if __name__ == "__main__":
    run()
