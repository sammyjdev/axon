from __future__ import annotations

from axon.router.llm_backend import resolve_litellm_model
from axon.router.provider_validation import provider_for_model


def test_provider_for_model_deepinfra() -> None:
    model_id = "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"
    assert provider_for_model(model_id) == "deepinfra"


def test_provider_for_model_unknown_prefix_still_anthropic() -> None:
    assert provider_for_model("weird/model") == "anthropic"


def test_provider_for_model_requires_the_slash_boundary() -> None:
    """The deepinfra branch must match the PREFIX `deepinfra/`, not the bare substring.
    Without the slash a lookalike vendor id (`deepinfrax/...`) would be routed to
    DeepInfra's rate-limit bucket and provider gate. Mutation sensor, issue #154.
    """
    assert provider_for_model("deepinfrax/some-model") == "anthropic"
    assert provider_for_model("deepinfra-foo/some-model") == "anthropic"


def test_resolve_litellm_model_deepinfra_passes_through() -> None:
    model_id = "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"
    assert resolve_litellm_model(model_id) == model_id
    assert resolve_litellm_model("phi3:mini") == "ollama/phi3:mini"
