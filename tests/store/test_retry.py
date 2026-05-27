"""Tests for axon.store.retry — exponential backoff retry helper (dec-112)."""

from __future__ import annotations

import asyncio

import pytest

from axon.store.retry import RetryExhausted, with_retry


class _Counter:
    def __init__(self, fail_until: int, exc: Exception) -> None:
        self.fail_until = fail_until
        self.exc = exc
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.fail_until:
            raise self.exc
        return "ok"


@pytest.mark.asyncio
class TestWithRetry:
    async def test_returns_immediately_when_fn_succeeds_first_try(self) -> None:
        c = _Counter(fail_until=0, exc=RuntimeError("never"))
        result = await with_retry(c, is_retryable=lambda e: True, max_budget_s=1.0)
        assert result == "ok"
        assert c.calls == 1

    async def test_retries_until_success(self) -> None:
        c = _Counter(fail_until=2, exc=ValueError("busy"))
        result = await with_retry(
            c,
            is_retryable=lambda e: isinstance(e, ValueError),
            max_budget_s=2.0,
            initial_delay_s=0.01,
            max_delay_s=0.05,
        )
        assert result == "ok"
        assert c.calls == 3

    async def test_raises_retry_exhausted_when_budget_runs_out(self) -> None:
        c = _Counter(fail_until=999, exc=ValueError("always busy"))
        with pytest.raises(RetryExhausted) as excinfo:
            await with_retry(
                c,
                is_retryable=lambda e: isinstance(e, ValueError),
                max_budget_s=0.2,
                initial_delay_s=0.05,
                max_delay_s=0.1,
            )
        assert isinstance(excinfo.value.__cause__, ValueError)
        assert c.calls >= 2

    async def test_does_not_retry_non_retryable_exception(self) -> None:
        c = _Counter(fail_until=1, exc=KeyError("fatal"))
        with pytest.raises(KeyError):
            await with_retry(
                c,
                is_retryable=lambda e: isinstance(e, ValueError),
                max_budget_s=1.0,
            )
        assert c.calls == 1

    async def test_jitter_keeps_delay_within_bounds(self) -> None:
        delays: list[float] = []
        real_sleep = asyncio.sleep

        async def fake_sleep(d: float) -> None:
            delays.append(d)
            await real_sleep(0)

        c = _Counter(fail_until=3, exc=ValueError("busy"))
        with pytest.MonkeyPatch().context() as m:
            m.setattr(asyncio, "sleep", fake_sleep)
            await with_retry(
                c,
                is_retryable=lambda e: True,
                max_budget_s=10.0,
                initial_delay_s=0.1,
                max_delay_s=1.0,
                multiplier=2.0,
                jitter_ratio=0.5,
            )
        # Delays should be positive and bounded by max_delay_s + jitter
        assert all(0 <= d <= 1.5 for d in delays)
        # Expected at least 3 sleeps (between 4 calls)
        assert len(delays) == 3
