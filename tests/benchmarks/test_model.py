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
    assert costs == sorted(costs)


def test_axon_turn_costs_are_flat_after_first():
    costs = turn_costs(_params(), mode="axon")
    assert len(costs) == 20
    assert costs[0] == 1500 + 2000
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


def test_default_session_totals_are_pinned():
    from benchmarks.model import DEFAULT_SESSION

    assert session_total(DEFAULT_SESSION, mode="baseline") == 87_000
    assert session_total(DEFAULT_SESSION, mode="axon") == 41_500


def test_session_params_rejects_zero_turns():
    with pytest.raises(ValueError):
        SessionParams(turns=0, base_context=1500, growth_per_turn=300, recall_budget=2000)


def test_session_params_rejects_negative_tokens():
    with pytest.raises(ValueError):
        SessionParams(turns=20, base_context=-1, growth_per_turn=300, recall_budget=2000)
