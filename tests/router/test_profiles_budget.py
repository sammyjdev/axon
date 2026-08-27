from __future__ import annotations

import pytest

from axon.router.profiles import available_profiles, get_profile
from axon.router.provider_validation import provider_for_model


def test_free_alias_resolves_to_budget() -> None:
    budget = get_profile("budget")
    free = get_profile("free")
    assert budget is free
    assert budget.name == "budget"

    # Normalization and default
    assert get_profile(" Free ") is budget
    assert get_profile(None) is budget

    with pytest.raises(ValueError, match="profile invalido: 'bogus'"):
        get_profile("bogus")


def test_available_profiles_includes_alias() -> None:
    assert available_profiles() == ["budget", "free", "paid"]


def test_budget_profile_models_are_live_measured_set() -> None:
    budget = get_profile("budget")
    expected_models = {
        "TRIVIAL_COMPLETION": "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "LOCAL_ONLY": "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "UNKNOWN": "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "CODE_ANALYSIS": "openrouter/meta-llama/llama-3.3-70b-instruct",
        "ARCHITECTURE": "deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "DEEP_REASONING": "deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo",
    }
    assert budget.models == expected_models
    assert set(budget.models.values()) == {
        "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "openrouter/meta-llama/llama-3.3-70b-instruct",
    }


def test_no_dead_ids_anywhere_in_specs() -> None:
    for name in ["budget", "paid"]:
        profile = get_profile(name)
        all_ids = (
            set(profile.models.values())
            | set(profile.cost_per_1k.keys())
            | {profile.classifier_model}
        )
        for model_id in all_ids:
            assert not model_id.startswith("groq/llama-"), f"Dead ID found in {name}: {model_id}"
            assert not model_id.startswith("nvidia_nim/"), f"Dead ID found in {name}: {model_id}"


def test_budget_cost_per_1k_measured_values() -> None:
    budget = get_profile("budget")
    expected_costs = {
        "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo": 4e-05,
        "deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo": 3.2e-04,
        "openrouter/meta-llama/llama-3.3-70b-instruct": 7.1e-04,
        # The classifier is routed to on every classification but was absent
        # here, so the budget guard billed it at 0.0 (engine.py uses
        # `_COST_PER_1K.get(model, 0.0)`). The sibling test below only walked
        # `models.values()`, which is how it went unnoticed.
        "groq/openai/gpt-oss-120b": 1.7e-04,
    }
    assert budget.cost_per_1k == expected_costs


def test_every_routed_model_has_nonzero_cost() -> None:
    budget = get_profile("budget")
    routed_models = set(budget.models.values())
    for model_id in routed_models:
        assert model_id in budget.cost_per_1k
        assert budget.cost_per_1k[model_id] > 0.0


def test_paid_cost_map_drops_dead_groq_entry() -> None:
    paid = get_profile("paid")
    assert "groq/llama-3.1-8b-instant" not in paid.cost_per_1k
    # Ensure other paid fields remain intact
    assert "openrouter/anthropic/claude-haiku-4" in paid.models.values()


def test_every_profile_has_three_distinct_tier_rungs() -> None:
    """
    Ensures that TRIVIAL_COMPLETION, CODE_ANALYSIS, and ARCHITECTURE are distinct.
    This is what makes the completion fallback a real downgrade (issue #100).
    Collapsing two rungs silently disables the top-tier failover.
    """
    for name in available_profiles():
        profile = get_profile(name)
        m = profile.models
        rungs = [m["TRIVIAL_COMPLETION"], m["CODE_ANALYSIS"], m["ARCHITECTURE"]]
        assert len(set(rungs)) == 3, f"Profile {name} has overlapping tier rungs: {rungs}"


def test_budget_top_tier_downgrade_crosses_providers() -> None:
    budget = get_profile("budget")
    top = budget.models["ARCHITECTURE"]
    mid = budget.models["CODE_ANALYSIS"]
    assert provider_for_model(top) == "deepinfra"
    assert provider_for_model(mid) == "openrouter"


@pytest.mark.parametrize("profile_name", ["budget", "paid"])
def test_every_model_a_profile_routes_to_carries_a_price(profile_name: str) -> None:
    """Including the classifier, which runs on every single classification.

    `engine.py` reads costs as `_COST_PER_1K.get(model, 0.0)`, so a model with
    no entry is billed as free and the budget guard undercounts it silently.
    dec-128 deleted a price for a model nothing routes to; the same rule in the
    other direction says a model that IS routed to must carry one.
    """
    profile = get_profile(profile_name)

    routed = set(profile.models.values()) | {profile.classifier_model}
    missing = sorted(m for m in routed if m not in profile.cost_per_1k)

    assert not missing, f"{profile_name} routes to unpriced models: {missing}"


@pytest.mark.parametrize("profile_name", ["budget", "paid"])
def test_no_price_is_carried_for_a_model_nothing_routes_to(profile_name: str) -> None:
    """The dec-128 rule itself: a price for an unroutable model is dead data."""
    profile = get_profile(profile_name)

    routed = set(profile.models.values()) | {profile.classifier_model}
    orphaned = sorted(m for m in profile.cost_per_1k if m not in routed)

    assert not orphaned, f"{profile_name} prices models it never routes to: {orphaned}"
