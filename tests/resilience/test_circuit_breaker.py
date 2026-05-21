from __future__ import annotations

import time

from axon.resilience.circuit_breaker import CircuitBreaker, CircuitState


def test_circuit_breaker_opens_after_threshold() -> None:
    breaker = CircuitBreaker(redis_url=None, failure_threshold=2, recovery_timeout_seconds=30)

    breaker.record_failure("provider:model")
    assert breaker.state("provider:model") is CircuitState.CLOSED

    breaker.record_failure("provider:model")
    assert breaker.state("provider:model") is CircuitState.OPEN
    assert breaker.allow_call("provider:model") is False


def test_circuit_breaker_half_open_after_timeout() -> None:
    breaker = CircuitBreaker(redis_url=None, failure_threshold=1, recovery_timeout_seconds=1)

    breaker.record_failure("provider:model")
    assert breaker.state("provider:model") is CircuitState.OPEN

    time.sleep(1.1)
    assert breaker.allow_call("provider:model") is True
    assert breaker.state("provider:model") is CircuitState.HALF_OPEN

    breaker.record_success("provider:model")
    assert breaker.state("provider:model") is CircuitState.CLOSED
