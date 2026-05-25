from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass

try:
    import redis
except Exception:  # pragma: no cover
    redis = None


@dataclass(frozen=True)
class RateLimitSpec:
    per_minute: int | None
    per_day: int | None

    @property
    def enforced(self) -> bool:
        return self.per_minute is not None or self.per_day is not None


def _optional_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def spec_from_env(provider: str) -> RateLimitSpec:
    """Le caps por provider via env. Ex.: provider='groq' -> AXON_GROQ_MAX_RPM/RPD."""
    prefix = f"AXON_{provider.upper()}"
    return RateLimitSpec(
        per_minute=_optional_int(f"{prefix}_MAX_RPM"),
        per_day=_optional_int(f"{prefix}_MAX_RPD"),
    )


class RateLimiter:
    """Fixed-window por minuto e por dia via Redis INCR+TTL (memoria como fallback).

    Simples, suficiente pra gates de free tier. Aceita burst-at-boundary;
    margem nos defaults compensa (ex: GROQ default 25 abaixo dos 30 reais).
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._memory: dict[str, int] = defaultdict(int)
        self._memory_expiry: dict[str, float] = {}
        self._redis = None
        if redis is not None and redis_url:
            try:
                self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
            except Exception:
                self._redis = None

    def allow_call(self, provider: str, spec: RateLimitSpec) -> bool:
        if not spec.enforced:
            return True
        now = time.time()
        minute_key = self._minute_key(provider, now)
        day_key = self._day_key(provider, now)

        minute_count = self._get(minute_key, now)
        day_count = self._get(day_key, now)

        if spec.per_minute is not None and minute_count >= spec.per_minute:
            return False
        if spec.per_day is not None and day_count >= spec.per_day:
            return False

        self._incr(minute_key, ttl=70, now=now)
        self._incr(day_key, ttl=86460, now=now)
        return True

    def usage(self, provider: str) -> tuple[int, int]:
        """Retorna (uso_no_minuto, uso_no_dia). Util pra debug/observabilidade."""
        now = time.time()
        return (
            self._get(self._minute_key(provider, now), now),
            self._get(self._day_key(provider, now), now),
        )

    def _minute_key(self, provider: str, now: float) -> str:
        bucket = int(now // 60)
        return f"rl:{provider}:m:{bucket}"

    def _day_key(self, provider: str, now: float) -> str:
        bucket = int(now // 86400)
        return f"rl:{provider}:d:{bucket}"

    def _get(self, key: str, now: float) -> int:
        if self._redis is not None:
            try:
                value = self._redis.get(key)
                return int(value) if value else 0
            except Exception:
                pass
        expiry = self._memory_expiry.get(key)
        if expiry is not None and now > expiry:
            self._memory.pop(key, None)
            self._memory_expiry.pop(key, None)
            return 0
        return self._memory.get(key, 0)

    def _incr(self, key: str, ttl: int, now: float) -> None:
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.incr(key, 1)
                pipe.expire(key, ttl)
                pipe.execute()
                return
            except Exception:
                pass
        self._memory[key] += 1
        self._memory_expiry[key] = now + ttl
