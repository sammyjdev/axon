"""Render the AXON token-savings comparison chart.

Plots cumulative input tokens vs turn number for baseline and AXON modes.
The chart annotates the modelling assumptions so it is not mistaken for an
instrumented capture.
"""

from __future__ import annotations

from datetime import date
from itertools import accumulate
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: no display needed
import matplotlib.pyplot as plt  # noqa: E402

from benchmarks.model import DEFAULT_SESSION, savings, turn_costs  # noqa: E402


def render_chart(output_dir: Path | str = "benchmarks/results") -> Path:
    """Render the comparison chart as a PNG and return its path."""
    p = DEFAULT_SESSION
    turns = list(range(1, p.turns + 1))
    baseline = list(accumulate(turn_costs(p, mode="baseline")))
    axon = list(accumulate(turn_costs(p, mode="axon")))

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(turns, baseline, marker="o", label="Without AXON")
    ax.plot(turns, axon, marker="o", label="With AXON")
    ax.set_xlabel("Coding-session turn")
    ax.set_ylabel("Cumulative input tokens")
    ax.set_title(f"AXON token savings - modelled {p.turns}-turn session")
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.text(
        0.02,
        0.97,
        f"Model: base {p.base_context} tok, +{p.growth_per_turn}/turn, "
        f"recall budget {p.recall_budget}/turn\nSavings: {savings(p):.1%}",
        transform=ax.transAxes,
        va="top",
        fontsize=8,
        bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "gray"},
    )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date.today():%Y-%m-%d}-token-savings.png"
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


if __name__ == "__main__":
    print(f"chart written: {render_chart()}")
