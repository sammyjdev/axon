from __future__ import annotations


def test_scoring_model_defaults_to_hosted_groq(monkeypatch) -> None:
    # dec-122 flag (USE_HOSTED_LOCAL_ROLES) makes the hosted backend the default.
    monkeypatch.delenv("AXON_SCORING_MODEL", raising=False)
    monkeypatch.delenv("AXON_SCORING_NUM_CTX", raising=False)
    from axon.config.runtime import load_runtime_config

    rt = load_runtime_config()
    assert rt.scoring_model == "groq/openai/gpt-oss-120b"
    assert rt.scoring_num_ctx == 8192


def test_scoring_model_from_env_is_resolved(monkeypatch) -> None:
    monkeypatch.setenv("AXON_SCORING_MODEL", "groq/openai/gpt-oss-120b")
    from axon.config.runtime import load_runtime_config

    rt = load_runtime_config()
    assert rt.scoring_model == "groq/openai/gpt-oss-120b"
