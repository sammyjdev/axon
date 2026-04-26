from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

try:
    import redis
except Exception:  # pragma: no cover
    redis = None


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class _Snapshot:
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    opened_at: float = 0.0
    half_open_calls: int = 0


class CircuitBreaker:
    def __init__(
        self,
        redis_url: str | None = None,
        failure_threshold: int = 3,
        recovery_timeout_seconds: int = 30,
        half_open_max_calls: int = 1,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._half_open_max_calls = half_open_max_calls
        self._memory: dict[str, _Snapshot] = {}
        self._redis = None
        if redis is not None and redis_url:
            try:
                self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
            except Exception:
                self._redis = None

    def allow_call(self, key: str) -> bool:
        snap = self._load(key)
        now = time.time()

        if snap.state is CircuitState.CLOSED:
            return True

        if snap.state is CircuitState.OPEN:
            if (now - snap.opened_at) >= self._recovery_timeout_seconds:
                snap.state = CircuitState.HALF_OPEN
                snap.half_open_calls = 0
                self._save(key, snap)
            else:
                return False

        if snap.state is CircuitState.HALF_OPEN:
            if snap.half_open_calls >= self._half_open_max_calls:
                return False
            snap.half_open_calls += 1
            self._save(key, snap)
            return True

        return True

    def record_success(self, key: str) -> None:
        self._save(key, _Snapshot())

    def record_failure(self, key: str) -> None:
        snap = self._load(key)

        if snap.state is CircuitState.HALF_OPEN:
            snap.state = CircuitState.OPEN
            snap.opened_at = time.time()
            snap.failures = self._failure_threshold
            self._save(key, snap)
            return

        snap.failures += 1
        if snap.failures >= self._failure_threshold:
            snap.state = CircuitState.OPEN
            snap.opened_at = time.time()
        self._save(key, snap)

    def state(self, key: str) -> CircuitState:
        return self._load(key).state

    def _redis_key(self, key: str) -> str:
        return f"cb:{key}"

    def _load(self, key: str) -> _Snapshot:
        if self._redis is not None:
            try:
                data = self._redis.hgetall(self._redis_key(key))
                if data:
                    return _Snapshot(
                        state=CircuitState(data.get("state", CircuitState.CLOSED.value)),
                        failures=int(data.get("failures", "0")),
                        opened_at=float(data.get("opened_at", "0")),
                        half_open_calls=int(data.get("half_open_calls", "0")),
                    )
            except Exception:
                pass
        return self._memory.get(key, _Snapshot())

    def _save(self, key: str, snap: _Snapshot) -> None:
        self._memory[key] = snap
        if self._redis is not None:
            try:
                self._redis.hset(
                    self._redis_key(key),
                    mapping={
                        "state": snap.state.value,
                        "failures": str(snap.failures),
                        "opened_at": str(snap.opened_at),
                        "half_open_calls": str(snap.half_open_calls),
                    },
                )
            except Exception:
                pass
