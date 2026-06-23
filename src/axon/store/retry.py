"""Retry helper with exponential backoff and jitter (dec-112).

Used to retry SQLite writes under SQLITE_BUSY before falling back to the
pending directory. Designed to be small and predictable; callers control
the retryable predicate so this helper stays domain-agnostic.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable


class RetryExhausted(Exception):
    """Raised when the retry budget is exhausted without a successful call."""


async def with_retry[T](
    fn: Callable[[], Awaitable[T]],
    *,
    is_retryable: Callable[[Exception], bool],
    max_budget_s: float = 10.0,
    initial_delay_s: float = 0.1,
    max_delay_s: float = 2.0,
    multiplier: float = 5.0,
    jitter_ratio: float = 0.5,
) -> T:
    """Run ``fn`` with exponential backoff + jitter until success or budget exhausted.

    Non-retryable exceptions propagate immediately. The total elapsed time
    (including sleep) is bounded by ``max_budget_s``. After exhaustion the
    last exception is wrapped in ``RetryExhausted``.
    """
    deadline = time.monotonic() + max_budget_s
    delay = initial_delay_s
    last_exc: Exception | None = None

    while True:
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 — predicate decides
            if not is_retryable(exc):
                raise
            last_exc = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RetryExhausted("retry budget exhausted") from last_exc
            jitter = delay * jitter_ratio * (random.random() * 2 - 1)
            sleep_for = max(0.0, min(delay + jitter, remaining))
            await asyncio.sleep(sleep_for)
            delay = min(delay * multiplier, max_delay_s)
