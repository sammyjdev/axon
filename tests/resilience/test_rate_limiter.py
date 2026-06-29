from __future__ import annotations

import time

import pytest

from axon.resilience.rate_limiter import RateLimiter, RateLimitSpec, spec_from_env


def test_spec_from_env_returns_none_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("AXON_GROQ_MAX_RPM", raising=False)
    monkeypatch.delenv("AXON_GROQ_MAX_RPD", raising=False)

    spec = spec_from_env("groq")

    assert spec.per_minute is None
    assert spec.per_day is None
    assert not spec.enforced


def test_spec_from_env_reads_caps(monkeypatch) -> None:
    monkeypatch.setenv("AXON_GROQ_MAX_RPM", "25")
    monkeypatch.setenv("AXON_GROQ_MAX_RPD", "13000")

    spec = spec_from_env("groq")

    assert spec.per_minute == 25
    assert spec.per_day == 13000
    assert spec.enforced


def test_spec_from_env_ignores_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("AXON_GROQ_MAX_RPM", "lol")
    monkeypatch.setenv("AXON_GROQ_MAX_RPD", "-5")

    spec = spec_from_env("groq")

    assert spec.per_minute is None
    assert spec.per_day is None


def test_allow_call_passes_when_no_caps() -> None:
    limiter = RateLimiter()
    spec = RateLimitSpec(per_minute=None, per_day=None)

    for _ in range(100):
        assert limiter.allow_call("groq", spec) is True


def test_allow_call_blocks_after_minute_cap() -> None:
    limiter = RateLimiter()
    spec = RateLimitSpec(per_minute=3, per_day=None)

    assert limiter.allow_call("groq", spec) is True
    assert limiter.allow_call("groq", spec) is True
    assert limiter.allow_call("groq", spec) is True
    assert limiter.allow_call("groq", spec) is False


def test_allow_call_blocks_after_daily_cap() -> None:
    limiter = RateLimiter()
    spec = RateLimitSpec(per_minute=None, per_day=2)

    assert limiter.allow_call("nvidia_nim", spec) is True
    assert limiter.allow_call("nvidia_nim", spec) is True
    assert limiter.allow_call("nvidia_nim", spec) is False


def test_usage_reports_current_counts() -> None:
    limiter = RateLimiter()
    spec = RateLimitSpec(per_minute=10, per_day=10)

    limiter.allow_call("groq", spec)
    limiter.allow_call("groq", spec)

    per_minute, per_day = limiter.usage("groq")
    assert per_minute == 2
    assert per_day == 2


def test_providers_are_isolated() -> None:
    limiter = RateLimiter()
    spec = RateLimitSpec(per_minute=1, per_day=None)

    assert limiter.allow_call("groq", spec) is True
    assert limiter.allow_call("groq", spec) is False
    # NIM mantem seu proprio bucket
    assert limiter.allow_call("nvidia_nim", spec) is True


def test_minute_bucket_resets_after_window(monkeypatch) -> None:
    limiter = RateLimiter()
    spec = RateLimitSpec(per_minute=2, per_day=None)

    base = time.time()
    monkeypatch.setattr("axon.resilience.rate_limiter.time.time", lambda: base)
    assert limiter.allow_call("groq", spec) is True
    assert limiter.allow_call("groq", spec) is True
    assert limiter.allow_call("groq", spec) is False

    # Avanca pra proxima janela de 60s — novo bucket, contador zera
    monkeypatch.setattr("axon.resilience.rate_limiter.time.time", lambda: base + 65)
    assert limiter.allow_call("groq", spec) is True


@pytest.mark.parametrize("provider", ["groq", "nvidia_nim", "openrouter"])
def test_spec_from_env_provider_naming(monkeypatch, provider) -> None:
    upper = provider.upper()
    monkeypatch.setenv(f"AXON_{upper}_MAX_RPM", "42")
    monkeypatch.delenv(f"AXON_{upper}_MAX_RPD", raising=False)

    spec = spec_from_env(provider)

    assert spec.per_minute == 42
    assert spec.per_day is None
