"""The embedder's HTTP behaviour against a real local server.

Measured 2026-10-15: opening the TLS connection took 0.29 s of a 0.64 s embed,
paid again on every search, and a provider 429 failed the whole repo's sync
(four roots on the 2026-10-14 reindex).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from axon.config.runtime import EmbedderProviderConfig
from axon.embedder import providers


class _Provider:
    """A loopback embeddings endpoint that answers from a script of statuses."""

    def __init__(self, statuses: list[int], retry_after: str = "0") -> None:
        self.statuses = list(statuses)
        self.connections: set[int] = set()
        self.requests = 0
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("content-length", 0)))
                provider.connections.add(self.client_address[1])
                provider.requests += 1
                status = provider.statuses.pop(0) if provider.statuses else 200
                body = json.dumps(
                    {"data": [{"embedding": [3.0, 4.0]}]} if status == 200 else {"error": "rate"}
                ).encode()
                self.send_response(status)
                if status == 429:
                    self.send_header("Retry-After", retry_after)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def config(self) -> EmbedderProviderConfig:
        return EmbedderProviderConfig(
            name="deepinfra",
            endpoint=f"http://127.0.0.1:{self.server.server_port}/v1/embeddings",
            model="BAAI/bge-m3",
            api_key_env="AXON_TEST_PROVIDER_KEY",
        )


@pytest.fixture
def provider_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AXON_TEST_PROVIDER_KEY", "test-key")


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def fresh_client() -> Generator[None, None, None]:
    """Each test starts with no open connection to a previous test's server."""
    providers.close_client()
    yield
    providers.close_client()


def _start(statuses: list[int], retry_after: str = "0") -> _Provider:
    return _Provider(statuses, retry_after)


def test_one_process_embeds_three_times_over_one_connection(
    provider_key: None, fresh_client: None
) -> None:
    server = _start([200, 200, 200])
    embed = providers.provider_fn(server.config)

    for text in ("one", "two", "three"):
        providers.embed_via_chain([text], providers=[embed])

    assert server.requests == 3
    assert len(server.connections) == 1


def test_a_rate_limited_provider_is_retried_until_it_answers(
    provider_key: None, fresh_client: None, waits: list[float]
) -> None:
    server = _start([429, 429, 200], retry_after="2")

    vectors = providers.embed_via_chain(["x"], providers=[providers.provider_fn(server.config)])

    assert vectors == [[0.6, 0.8]]
    assert server.requests == 3
    assert waits == [2.0, 2.0]


def test_a_provider_still_rate_limited_after_three_attempts_fails(
    provider_key: None, fresh_client: None, waits: list[float]
) -> None:
    server = _start([429, 429, 429, 429])

    with pytest.raises(providers.AllProvidersFailedError):
        providers.embed_via_chain(["x"], providers=[providers.provider_fn(server.config)])

    assert server.requests == 3


def test_a_long_retry_after_is_capped(
    provider_key: None, fresh_client: None, waits: list[float]
) -> None:
    server = _start([429, 200], retry_after="3600")

    providers.embed_via_chain(["x"], providers=[providers.provider_fn(server.config)])

    assert waits == [providers.MAX_RETRY_WAIT_S]
