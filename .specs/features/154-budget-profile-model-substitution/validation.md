## Validation: issue #154 - PASS
Spec-anchored check: no spec.md for this issue (entered `task` directly); fell back to "assertion exists and covers the criterion" - all four "Done when" criteria are covered by `tests/router/test_profiles_budget.py`, `tests/router/test_provider_deepinfra.py` and `tests/config/test_provider_profile_alias.py`.
Mutation sensor (mandatory): EMPTY_RETURN=KILLED, IDENTITY_RETURN=KILLED, NEGATE_CONDITIONAL=KILLED, DROP_SIDE_EFFECT=N/A: the changed units (`profiles.get_profile`/`available_profiles`, `provider_validation.provider_for_model`, `llm_backend.resolve_litellm_model`) are pure - no write, state mutation, event or persisted record to remove.
Mutation sensor (extras): [8 injected, 8 killed, 0 survived: none]
Report: .specs/features/154-budget-profile-model-substitution/validation.md

### Battery detail (baseline: 169 passed on `tests/router tests/config`)

| operator | mutation | verdict | killing tests |
|---|---|---|---|
| EMPTY_RETURN | `available_profiles()` -> `[]` | KILLED | 9 collection errors (parametrized over `available_profiles()`) |
| IDENTITY_RETURN | `provider_for_model` returns `model` unchanged | KILLED | 6 failed, incl. `test_provider_for_model_maps_known_prefixes`, `test_budget_top_tier_downgrade_crosses_providers` |
| NEGATE_CONDITIONAL | `if not model.startswith("deepinfra/")` | KILLED | 5 failed, incl. `test_provider_for_model_deepinfra`, `test_provider_for_model_unknown_prefix_still_anthropic` |
| DROP_SIDE_EFFECT | - | N/A | pure units, see above |
| TIER_COLLAPSE | `CODE_ANALYSIS` -> the ARCHITECTURE 70B id | KILLED | 8 failed: `test_engine_completion_fallback.py::test_architecture_tier_falls_over_to_mid_tier`, `::test_deep_reasoning_tier_also_falls_over_to_mid_tier`, `::test_fallback_denied_by_breaker_never_calls_fallback_model`, both `test_engine_completion_fallback_usage_shape.py` cases, `test_profiles_budget.py::test_budget_profile_models_are_live_measured_set` |
| ALIAS_DROP | remove `"free": _BUDGET` from `_REGISTRY` | KILLED | 9 collection errors (alias tests + parametrization) |
| PROVIDER_PREFIX_BOUNDARY (no slash) | `startswith("deepinfra")` | KILLED | `test_provider_deepinfra.py::test_provider_for_model_requires_the_slash_boundary` - the survivor from the previous run is now closed |
| PROVIDER_PREFIX (misspelled `deep_infra/`) | branch never fires | KILLED | `test_provider_for_model_deepinfra`, `test_budget_top_tier_downgrade_crosses_providers` |
| COST_ZEROING | 70B `cost_per_1k` -> `0.0` | KILLED | `test_budget_cost_per_1k_measured_values`, `test_every_routed_model_has_nonzero_cost` |
| EXCEPTION_SWALLOW | `except KeyError: return _PAID` | KILLED | `test_free_alias_resolves_to_budget`, `test_router.py::test_unknown_profile_raises` |
| KNOWN_PROVIDER_DROP | drop `"deepinfra/"` from `_KNOWN_PROVIDERS` | KILLED | `test_resolve_litellm_model_deepinfra_passes_through` |
| DEFAULT_KEY | `get_profile` default `"budget"` -> `"paid"` | KILLED | `test_free_alias_resolves_to_budget` |

Every mutation was applied and reverted with an in-place file edit (byte-for-byte restore asserted after each revert); `git checkout` was never used. Tree after the run: 13 modified files, 87 insertions, 56 deletions; gate back to 169 passed.
