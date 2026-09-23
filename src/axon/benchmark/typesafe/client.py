"""Minimal TypeSafe SystemOne client for reproducible benchmark measurements.

Responses are cached by canonical request content, so a repeated measurement is
offline and free. This client intentionally exposes the provider's answer map
unchanged; benchmark arms interpret it in their own measurement code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from axon.benchmark.typesafe import questions

_sleep = asyncio.sleep


class TypeSafeError(RuntimeError):
    """A TypeSafe API request failed."""


@dataclass(frozen=True)
class Answer:
    """One measured call, whatever the arm."""

    payload: dict
    model: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    cost_usd: float
    cached: bool


class JsonCache:
    """Durable dict on disk shared by benchmark arms."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._values: dict[str, dict] = (
            json.loads(path.read_text()) if path.exists() else {}
        )

    def get(self, key: str) -> dict | None:
        return self._values.get(key)

    def set(self, key: str, value: dict) -> None:
        self._values[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._values, sort_keys=True))


def cache_key(*, state: Any, question_map: Any, model: str, sample_index: int) -> str:
    """Return a stable key over the full measured request."""
    canonical = json.dumps(
        {
            "state": state,
            "questions": question_map,
            "model": model,
            "sample_index": sample_index,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class TypeSafeClient:
    def __init__(
        self,
        *,
        cache: JsonCache,
        api_key: str | None = None,
        model: str = questions.MODEL,
        transport: httpx.AsyncBaseTransport | None = None,
        max_concurrency: int = 8,
        max_attempts: int = 5,
    ) -> None:
        self.cache = cache
        self.api_key = api_key if api_key is not None else os.environ["TYPESAFE_API_KEY"]
        self.model = model
        self.transport = transport
        self.max_attempts = max_attempts
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def ask(self, state: Any, question_map: dict, *, sample_index: int = 0) -> Answer:
        """POST state and questions, returning a parsed measured response."""
        key = cache_key(
            state=state,
            question_map=question_map,
            model=self.model,
            sample_index=sample_index,
        )
        cached = self.cache.get(key)
        if cached is not None:
            return Answer(
                payload=cached["payload"],
                model=cached["model"],
                input_tokens=cached["input_tokens"],
                output_tokens=cached["output_tokens"],
                # No time was spent this run, so latency is honestly zero. Cost is
                # not: the tokens were paid for, and a report whose cost column
                # flips to zero on a rerun stops being comparable to the one
                # before it. Recomputed from the pinned tokens and price.
                latency_s=0.0,
                cost_usd=cached["input_tokens"] / 1_000_000 * questions.PRICE_PER_MTOK_INPUT,
                cached=True,
            )

        for attempt in range(self.max_attempts):
            started = time.perf_counter()
            async with self._semaphore, httpx.AsyncClient(transport=self.transport) as http:
                response = await http.post(
                    questions.ENDPOINT,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"state": state, "questions": question_map, "model": self.model},
                )
            latency_s = time.perf_counter() - started
            if response.status_code == 200:
                raw = response.json()
                usage = raw["usage"]
                input_tokens = int(usage["input_tokens"])
                output_tokens = int(usage["output_tokens"])
                value = {
                    "payload": raw["answers"],
                    "model": raw["model"],
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
                self.cache.set(key, value)
                return Answer(
                    **value,
                    latency_s=latency_s,
                    cost_usd=input_tokens / 1_000_000 * questions.PRICE_PER_MTOK_INPUT,
                    cached=False,
                )
            if response.status_code in (401, 422):
                raise TypeSafeError(f"TypeSafe API {response.status_code}: {response.text}")
            if response.status_code not in (429, 529) or attempt == self.max_attempts - 1:
                raise TypeSafeError(f"TypeSafe API {response.status_code}: {response.text}")
            await _sleep(_retry_delay(response, attempt))
        raise TypeSafeError("TypeSafe API retry attempts exhausted")


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after is not None:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return float(2**attempt)
