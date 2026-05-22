# AXON Phase 7 — Token Savings Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build a reproducible benchmark that quantifies the token economy AXON provides across a multi-turn coding session, and render it as a chart.

**Architecture:** A pure, deterministic token model (`benchmarks/model.py`) computes per-turn and cumulative token cost for two modes — `baseline` (no AXON: project context is re-supplied inline every turn, growing each turn) and `axon` (context retrieved once, then a fixed-budget recall per turn). Two thin scenario runners print the metrics; a visualizer renders the comparison chart. The model is the only logic with branching, so it gets full TDD; the runners and visualizer are thin.

**Honesty constraint:** The benchmark is an explicit *model* of session token cost, not an instrumented live capture. Every script and the chart must state the assumptions (turn count, per-turn context growth, recall budget). Report whatever savings the model yields — do NOT tune constants to hit a target. If it lands below 60%, the report says so.

**Tech Stack:** Python 3.11+, pytest, matplotlib (new `bench` optional-dependency extra).

---

## File Structure

- Create `benchmarks/__init__.py` — marks the package.
- Create `benchmarks/model.py` — pure token model: `SessionParams`, `turn_costs()`, `session_total()`, `savings()`.
- Create `benchmarks/scenarios/__init__.py`
- Create `benchmarks/scenarios/long_session_baseline.py` — runner: prints baseline metrics.
- Create `benchmarks/scenarios/long_session_axon.py` — runner: prints axon metrics + savings vs baseline.
- Create `benchmarks/visualize.py` — renders cumulative-tokens-vs-turn chart to `benchmarks/results/`.
- Create `tests/benchmarks/test_model.py` — TDD for the model.
- Modify `pyproject.toml` — add `bench` optional-dependency extra with `matplotlib`.
- Create `Makefile` — `bench` target.

---

## Task 1: Token model

**Files:**
- Create: `benchmarks/__init__.py`, `benchmarks/model.py`
- Test: `tests/benchmarks/test_model.py`

The model: a session of `turns` turns. `base_context` tokens of project context exist at the start. In `baseline` mode every turn re-supplies the project context, which has grown by `growth_per_turn` tokens for each prior turn (decisions accumulate); turn `k` (1-indexed) costs `base_context + growth_per_turn * (k - 1)`. In `axon` mode the project context is retrieved once at turn 1 (cost `base_context`), and every turn — including turn 1 — issues one recall of fixed cost `recall_budget`; turn `k` costs `recall_budget`, plus `base_context` added once at turn 1.

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/test_model.py
import pytest

from benchmarks.model import SessionParams, savings, session_total, turn_costs


def _params():
    return SessionParams(
        turns=20, base_context=1500, growth_per_turn=300, recall_budget=2000
    )


def test_baseline_turn_costs_grow_each_turn():
    costs = turn_costs(_params(), mode="baseline")
    assert len(costs) == 20
    assert costs[0] == 1500
    assert costs[1] == 1800
    assert costs[19] == 1500 + 300 * 19
    assert costs == sorted(costs)  # monotonically non-decreasing


def test_axon_turn_costs_are_flat_after_first():
    costs = turn_costs(_params(), mode="axon")
    assert len(costs) == 20
    assert costs[0] == 1500 + 2000  # base context retrieved once + first recall
    assert costs[1] == 2000
    assert costs[19] == 2000


def test_session_total_sums_turn_costs():
    p = _params()
    assert session_total(p, mode="baseline") == sum(turn_costs(p, mode="baseline"))
    assert session_total(p, mode="axon") == sum(turn_costs(p, mode="axon"))


def test_savings_is_fraction_reduced():
    p = _params()
    base = session_total(p, mode="baseline")
    axon = session_total(p, mode="axon")
    assert savings(p) == pytest.approx(1 - axon / base)
    assert 0.0 < savings(p) < 1.0


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        turn_costs(_params(), mode="bogus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy "python3 -m pytest tests/benchmarks/test_model.py -q"`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmarks'`

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/__init__.py
"""AXON token-savings benchmark (Phase 7)."""
```

```python
# benchmarks/model.py
"""Deterministic token-cost model for an AXON benchmark session.

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
    """Fraction of baseline tokens removed by AXON (0.0–1.0)."""
    base = session_total(params, mode="baseline")
    axon = session_total(params, mode="axon")
    return 1.0 - axon / base
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy "python3 -m pytest tests/benchmarks/test_model.py -q"`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add benchmarks/__init__.py benchmarks/model.py tests/benchmarks/test_model.py
git commit -m "feat: token-savings benchmark model (T7.1)"
```
End the commit message with a blank line then:
`Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`

---

## Task 2: Scenario runners

**Files:**
- Create: `benchmarks/scenarios/__init__.py`, `benchmarks/scenarios/long_session_baseline.py`, `benchmarks/scenarios/long_session_axon.py`
- Test: `tests/benchmarks/test_scenarios.py`

Both runners use the same default `SessionParams` so the comparison is fair. The default is defined once in `benchmarks/model.py`.

- [ ] **Step 1: Add the shared default to `benchmarks/model.py`**

Append to `benchmarks/model.py`:

```python
# Default session assumptions, shared by both scenario runners so the
# baseline/axon comparison uses identical inputs.
DEFAULT_SESSION = SessionParams(
    turns=20, base_context=1500, growth_per_turn=300, recall_budget=2000
)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/benchmarks/test_scenarios.py
from benchmarks.scenarios import long_session_axon, long_session_baseline


def test_baseline_run_reports_total(capsys):
    total = long_session_baseline.run()
    out = capsys.readouterr().out
    assert total > 0
    assert "baseline" in out.lower()
    assert str(total) in out


def test_axon_run_reports_total_and_savings(capsys):
    result = long_session_axon.run()
    out = capsys.readouterr().out
    assert result["axon_total"] > 0
    assert result["baseline_total"] > result["axon_total"]
    assert 0.0 < result["savings"] < 1.0
    assert "savings" in out.lower()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `rtk proxy "python3 -m pytest tests/benchmarks/test_scenarios.py -q"`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmarks.scenarios'`

- [ ] **Step 4: Write minimal implementation**

```python
# benchmarks/scenarios/__init__.py
"""Benchmark scenario runners."""
```

```python
# benchmarks/scenarios/long_session_baseline.py
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
```

```python
# benchmarks/scenarios/long_session_axon.py
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
    print(
        f"[axon] savings vs baseline ({baseline_total} tokens): {frac:.1%}"
    )
    return {
        "baseline_total": baseline_total,
        "axon_total": axon_total,
        "savings": frac,
    }


if __name__ == "__main__":
    run()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `rtk proxy "python3 -m pytest tests/benchmarks/test_scenarios.py -q"`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add benchmarks/model.py benchmarks/scenarios/ tests/benchmarks/test_scenarios.py
git commit -m "feat: benchmark scenario runners (T7.2)"
```
End with a blank line then the `Co-Authored-By` trailer.

---

## Task 3: Visualizer + `bench` extra + Makefile

**Files:**
- Create: `benchmarks/visualize.py`
- Test: `tests/benchmarks/test_visualize.py`
- Modify: `pyproject.toml`
- Create: `Makefile`

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/test_visualize.py
import pytest

matplotlib = pytest.importorskip("matplotlib")

from benchmarks.visualize import render_chart


def test_render_chart_writes_png(tmp_path):
    out = render_chart(output_dir=tmp_path)
    assert out.exists()
    assert out.suffix == ".png"
    assert out.stat().st_size > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy "python3 -m pytest tests/benchmarks/test_visualize.py -q"`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmarks.visualize'` (or skipped if matplotlib absent — install it first: `pip install matplotlib`).

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/visualize.py
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
    ax.set_title(f"AXON token savings — modelled {p.turns}-turn session")
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
```

- [ ] **Step 4: Add the `bench` extra to `pyproject.toml`**

In `pyproject.toml`, under `[project.optional-dependencies]`, after the `dev = [...]` block, add:

```toml
bench = [
    "matplotlib>=3.8",
]
```

- [ ] **Step 5: Create the `Makefile`**

```makefile
# AXON developer tasks
.PHONY: bench test

bench:  ## Run the token-savings benchmark and render the chart
	python3 -m benchmarks.scenarios.long_session_baseline
	python3 -m benchmarks.scenarios.long_session_axon
	python3 -m benchmarks.visualize

test:  ## Run the test suite
	python3 -m pytest tests/ -q
```

(Use a TAB for recipe indentation, not spaces.)

- [ ] **Step 6: Run test to verify it passes**

Install matplotlib if needed: `pip install 'matplotlib>=3.8'`
Run: `rtk proxy "python3 -m pytest tests/benchmarks/ -q"`
Expected: PASS (all benchmark tests).
Then run `make bench` and confirm it prints baseline + axon metrics and writes a PNG under `benchmarks/results/`.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/visualize.py tests/benchmarks/test_visualize.py pyproject.toml Makefile
git commit -m "feat: benchmark visualizer + make bench (T7.3)"
```
End with a blank line then the `Co-Authored-By` trailer.

---

## Task 4: Reproducibility check + results README

**Files:**
- Create: `benchmarks/README.md`
- Create: `benchmarks/results/.gitkeep`

- [ ] **Step 1: Run the full benchmark and capture the real number**

Run `make bench`. Note the actual savings percentage printed by the axon scenario.

- [ ] **Step 2: Write `benchmarks/README.md`**

Document, with the REAL number from Step 1: what the benchmark models, the assumptions (turn count, growth, recall budget), how to run it (`make bench`), and the measured savings. State explicitly that it is a model. If savings < 60%, say so plainly and do not claim otherwise.

- [ ] **Step 3: Keep the results dir tracked**

Create an empty `benchmarks/results/.gitkeep` so the output directory exists. Add `benchmarks/results/*.png` to `.gitignore` (generated artifacts are not committed; the chart is regenerated by `make bench`).

- [ ] **Step 4: Verify**

Run `rtk proxy "python3 -m pytest tests/ -q"` — full suite still green.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/README.md benchmarks/results/.gitkeep .gitignore
git commit -m "docs: benchmark README + reproducibility (T7.3 done check)"
```
End with a blank line then the `Co-Authored-By` trailer.

---

## Self-Review

**Spec coverage** — master plan Phase 7: T7.1 baseline scenario → Tasks 1+2; T7.2 axon scenario → Tasks 1+2; T7.3 visualize + `make bench` reproducibility + >60% target → Tasks 3+4. The >60% "done when" is reported honestly from the model output, not asserted as a test (the benchmark must not be rigged).

**Placeholder scan** — all code steps contain complete code.

**Type consistency** — `SessionParams`/`turn_costs`/`session_total`/`savings`/`DEFAULT_SESSION` names are consistent across model, scenarios, and visualizer. `render_chart` accepts `output_dir`.

**Honesty note** — no test asserts a specific savings threshold; `benchmarks/README.md` reports whatever the model yields.
