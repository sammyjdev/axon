from __future__ import annotations

import json


class GraphStore:
    def __init__(self, url: str = "redis://localhost:6379") -> None:
        self._url = url
        self._client = None

    def _client_conn(self):
        if self._client is None:
            import redis

            self._client = redis.from_url(self._url)
        return self._client

    def set_calls(self, symbol: str, calls: list[str]) -> None:
        self._client_conn().hset(f"dep:{symbol}", "calls", json.dumps(calls))

    def set_called_by(self, symbol: str, called_by: list[str]) -> None:
        self._client_conn().hset(f"dep:{symbol}", "called_by", json.dumps(called_by))

    def get_calls(self, symbol: str) -> list[str]:
        raw = self._client_conn().hget(f"dep:{symbol}", "calls")
        return json.loads(raw) if raw else []

    def get_called_by(self, symbol: str) -> list[str]:
        raw = self._client_conn().hget(f"dep:{symbol}", "called_by")
        return json.loads(raw) if raw else []

    def describe(self, symbol: str) -> str:
        calls = self.get_calls(symbol)
        called_by = self.get_called_by(symbol)
        return f"{symbol} calls {calls}, called by {called_by}"


def build_dependency_graph(symbols: dict[str, list[str]]) -> dict[str, list[str]]:
    reverse: dict[str, list[str]] = {}
    for caller, callees in symbols.items():
        for callee in callees:
            reverse.setdefault(callee, []).append(caller)
    return reverse
