"""bge-m3 provider chain: Ollama -> DeepInfra (EMB-2; NIM opt-in, endpoint broken upstream).

Thin per-provider HTTP adapters behind one `ProviderFn` interface, tried in
configured order with fall-through on error. See
.superpowers/sdd/briefs/emb-2-brief.md and the EMB-2 report for the
litellm-vs-adapter decision.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from axon.config.runtime import (
    EmbedderProviderConfig,
    credential_from_store,
    load_embedder_chain_config,
)

logger = logging.getLogger(__name__)

ProviderFn = Callable[[list[str]], list[list[float]]]

_TIMEOUT_SECONDS = 30.0

#: A provider answering 429 is asked again, this many attempts in all, waiting
#: what its Retry-After says up to MAX_RETRY_WAIT_S. The 2026-10-14 reindex hit
#: 429 on four roots, and each one failed that root's whole sync.
MAX_ATTEMPTS = 3
MAX_RETRY_WAIT_S = 10.0
_DEFAULT_RETRY_WAIT_S = 1.0

_client: Any = None
_client_lock = threading.Lock()


def _http() -> Any:
    """One HTTP client per process, so a long-lived MCP server keeps its
    connection to the provider. Measured 2026-10-15: opening it took 0.29 s of
    a 0.64 s embed, paid again on every search."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                import httpx

                _client = httpx.Client()
    return _client


def close_client() -> None:
    """Close the shared client; the next call opens a new one."""
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


def _retry_wait(retry_after: str | None) -> float:
    """Seconds to wait for a Retry-After header: delta-seconds or an HTTP-date."""
    if retry_after is None:
        seconds = _DEFAULT_RETRY_WAIT_S
    else:
        try:
            seconds = float(retry_after)
        except ValueError:
            try:
                until = parsedate_to_datetime(retry_after)
                seconds = (until - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError):  # neither form: wait the default
                seconds = _DEFAULT_RETRY_WAIT_S
    return min(max(seconds, 0.0), MAX_RETRY_WAIT_S)


def _post(url: str, *, timeout: float, **kwargs: Any) -> Any:
    attempt = 1
    while True:
        resp = _http().post(url, timeout=timeout, **kwargs)
        if resp.status_code == 429 and attempt < MAX_ATTEMPTS:
            time.sleep(_retry_wait(resp.headers.get("retry-after")))
            attempt += 1
            continue
        resp.raise_for_status()
        return resp


class AllProvidersFailedError(RuntimeError):
    """Raised when every provider in the bge-m3 chain fails.

    Never silently return a wrong-dim/empty/zero vector -- callers must handle
    this explicitly (e.g. surface an ingest/query error).
    """


class MissingApiKeyError(RuntimeError):
    """Raised when an embedding provider requires an API key that is not set."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not set")
        self.name = name


def _l2_normalize(vectors: list[list[float]]) -> list[list[float]]:
    normalized: list[list[float]] = []
    for vec in vectors:
        norm = math.sqrt(sum(x * x for x in vec))
        normalized.append([x / norm for x in vec] if norm > 0 else list(vec))
    return normalized


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _call_ollama(
    texts: list[str],
    config: EmbedderProviderConfig,
    timeout: float = _TIMEOUT_SECONDS,
) -> list[list[float]]:
    resp = _post(
        config.endpoint,
        json={"model": config.model, "input": texts},
        timeout=timeout,
    )
    return resp.json()["embeddings"]


def _call_openai_compatible(
    texts: list[str],
    config: EmbedderProviderConfig,
    timeout: float = _TIMEOUT_SECONDS,
) -> list[list[float]]:
    if not config.api_key_env:
        api_key = ""
    else:
        # Stripped: httpx rejects a header value with trailing whitespace and quotes the
        # whole value in the error, which embed_via_chain logs and re-raises.
        env_value = (os.environ.get(config.api_key_env) or "").strip()
        api_key = env_value or (credential_from_store(config.api_key_env) or "").strip()
        if not api_key:
            raise MissingApiKeyError(config.api_key_env)
    resp = _post(
        config.endpoint,
        json={"model": config.model, "input": texts},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    payload = resp.json()
    return [item["embedding"] for item in payload["data"]]


_CALLERS: dict[
    str, Callable[[list[str], EmbedderProviderConfig, float], list[list[float]]]
] = {
    "ollama": _call_ollama,
    "nim": _call_openai_compatible,
    "deepinfra": _call_openai_compatible,
}


def provider_fn(
    config: EmbedderProviderConfig,
    timeout: float = _TIMEOUT_SECONDS,
) -> ProviderFn:
    """Build a callable that embeds texts via a single configured provider."""
    caller = _CALLERS[config.name]
    return lambda texts: caller(texts, config, timeout)


def embed_via_chain(
    texts: list[str],
    providers: list[ProviderFn] | None = None,
) -> list[list[float]]:
    """Embed via the ordered bge-m3 provider chain.

    Tries providers in order; falls through to the next on any error/timeout.
    Raises AllProvidersFailedError if every provider fails. Returned vectors
    are L2-normalized regardless of which provider served the request.
    """
    if providers is None:
        chain = load_embedder_chain_config()
        providers = [provider_fn(cfg) for cfg in chain.providers]

    errors: list[str] = []
    for fn in providers:
        try:
            vectors = fn(texts)
            if len(vectors) != len(texts):
                raise ValueError(
                    f"provider returned {len(vectors)} vectors for {len(texts)} texts"
                )
            normalized = _l2_normalize(vectors)
        except Exception as exc:  # noqa: BLE001 - any provider failure must fall through
            errors.append(str(exc))
            logger.warning(
                "bge-m3 embedding provider failed, falling through: %s", exc, exc_info=True
            )
            continue
        return normalized
    raise AllProvidersFailedError(
        f"All bge-m3 embedding providers failed: {'; '.join(errors) or 'no providers configured'}"
    )


def check_provider_interchangeable(
    local_provider: ProviderFn,
    candidate_provider: ProviderFn,
    sample_texts: list[str] | None = None,
    threshold: float = 0.999,
) -> bool:
    """Onboarding check: embed a fixed sample via the local and a candidate provider,
    assert pairwise cosine similarity >= threshold (guards normalization/float drift).

    Cosine similarity is scale-invariant, so the raw provider vectors are compared
    directly -- no pre-normalization needed here (embed_via_chain owns L2-norm).
    """
    sample = sample_texts or ["axon bge-m3 onboarding sample text"]
    local_vecs = local_provider(sample)
    candidate_vecs = candidate_provider(sample)
    return all(
        cosine_similarity(lv, cv) >= threshold
        for lv, cv in zip(local_vecs, candidate_vecs, strict=True)
    )
