from __future__ import annotations

import axon.mcp.server as server


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
