from __future__ import annotations

import dataclasses

import axon.mcp.server as server
from axon.context.contracts import DEFAULT_RETRIEVAL_STRATEGIES, ContextPack


def test_self_correct_enabled_default_on(monkeypatch):
    monkeypatch.delenv("AXON_SELF_CORRECT", raising=False)
    assert server._self_correct_enabled() is True


def test_self_correct_kill_switch_off(monkeypatch):
    monkeypatch.setenv("AXON_SELF_CORRECT", "0")
    assert server._self_correct_enabled() is False


def test_judge_sufficiency_parses_true(monkeypatch):
    class _Msg:
        content = '{"sufficient": true}'

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    monkeypatch.setattr(server.litellm, "completion", lambda **kw: _Resp())
    assert server._judge_sufficiency("q", "ctx") is True


def test_judge_sufficiency_false_on_malformed(monkeypatch):
    class _Msg:
        content = "not json"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    monkeypatch.setattr(server.litellm, "completion", lambda **kw: _Resp())
    # Malformed judge output must not crash ask(); default to insufficient
    # (conservative: prefer a retry over trusting an unparseable verdict).
    assert server._judge_sufficiency("q", "ctx") is False


def test_self_correct_model_defaults_to_bottom_tier(monkeypatch):
    monkeypatch.delenv("AXON_SELF_CORRECT_MODEL", raising=False)
    assert server._self_correct_model() == server._bottom_tier_model()


def test_self_correct_model_env_override(monkeypatch):
    monkeypatch.setenv("AXON_SELF_CORRECT_MODEL", "openrouter/meta-llama/llama-3.1-8b-instruct")
    assert server._self_correct_model() == "openrouter/meta-llama/llama-3.1-8b-instruct"


def test_cheap_llm_json_uses_self_correct_model(monkeypatch):
    monkeypatch.setenv("AXON_SELF_CORRECT_MODEL", "openrouter/meta-llama/llama-3.1-8b-instruct")
    captured = {}

    class _Msg:
        content = "{}"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    def _capture(**kw):
        captured["model"] = kw.get("model")
        return _Resp()

    monkeypatch.setattr(server.litellm, "completion", _capture)
    server._cheap_llm_json("sys", "user")
    assert captured["model"] == "openrouter/meta-llama/llama-3.1-8b-instruct"


def test_augment_pack_fn_appends_graph_segment_without_mutating_original():
    strategy = DEFAULT_RETRIEVAL_STRATEGIES["balanced"]
    pack = ContextPack(
        strategy=strategy,
        task_type="CODE_ANALYSIS",
        profile="solo-dev",
        mode="hybrid-local",
        contexts=("knowledge",),
        segments=("a",),
    )

    new_pack = dataclasses.replace(pack, segments=pack.segments + ("graph",))

    assert "graph" in new_pack.text
    assert "graph" not in pack.text
