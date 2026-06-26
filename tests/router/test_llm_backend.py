from __future__ import annotations

from axon.router.llm_backend import (
    default_compressor_model,
    default_scoring_model,
    litellm_kwargs,
    resolve_litellm_model,
)


def test_hosted_defaults_when_flag_on() -> None:
    # dec-122: the decided hosted backends are active by default, no env needed.
    assert default_scoring_model() == "groq/openai/gpt-oss-120b"
    assert default_compressor_model() == "cerebras/gpt-oss-120b"


def test_local_defaults_when_flag_off(monkeypatch) -> None:
    monkeypatch.setattr("axon.router.llm_backend.USE_HOSTED_LOCAL_ROLES", False)
    assert default_scoring_model() == "gemma4:e4b"
    assert default_compressor_model() == "phi3:mini"


def test_bare_ollama_name_gets_ollama_prefix() -> None:
    assert resolve_litellm_model("phi3:mini") == "ollama/phi3:mini"
    assert resolve_litellm_model("gemma4:e4b") == "ollama/gemma4:e4b"


def test_full_litellm_id_is_left_alone() -> None:
    assert resolve_litellm_model("groq/openai/gpt-oss-120b") == "groq/openai/gpt-oss-120b"
    assert resolve_litellm_model("cerebras/gpt-oss-120b") == "cerebras/gpt-oss-120b"
    assert resolve_litellm_model("ollama/phi3:mini") == "ollama/phi3:mini"


def test_ollama_model_gets_api_base_and_num_ctx() -> None:
    kw = litellm_kwargs("ollama/phi3:mini", ollama_host="http://desktop:11434", num_ctx=8192)
    assert kw["model"] == "ollama/phi3:mini"
    assert kw["api_base"] == "http://desktop:11434"
    assert kw["extra_body"] == {"options": {"num_ctx": 8192}}


def test_cloud_model_gets_no_api_base_or_num_ctx() -> None:
    kw = litellm_kwargs(
        "groq/openai/gpt-oss-120b", ollama_host="http://desktop:11434", num_ctx=8192
    )
    assert kw["model"] == "groq/openai/gpt-oss-120b"
    assert "api_base" not in kw
    assert "extra_body" not in kw
