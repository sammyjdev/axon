"""Live embedder probe check for axon doctor."""

from __future__ import annotations

import errno
from collections.abc import Callable

import httpx

from axon.config.runtime import (
    EmbedderProviderConfig,
    load_embedder_chain_config,
)
from axon.doctor import CheckResult, CheckStatus
from axon.embedder.providers import MissingApiKeyError, provider_fn

ProbeFn = Callable[[EmbedderProviderConfig, float], None]

_PROBE_TEXT = "axon doctor embedder probe"
_PROBE_TIMEOUT_S = 10.0


def _probe(config: EmbedderProviderConfig, timeout: float) -> None:
    provider_fn(config, timeout=timeout)([_PROBE_TEXT])


def _is_network_denied(exc: BaseException) -> bool:
    visited: set[int] = set()
    stack: list[BaseException] = [exc]
    depth = 0
    max_depth = 16
    while stack and depth < max_depth:
        depth += 1
        curr = stack.pop()
        if id(curr) in visited:
            continue
        visited.add(id(curr))
        if isinstance(curr, OSError) and getattr(curr, "errno", None) in (
            errno.EPERM,
            errno.EACCES,
        ):
            return True
        if curr.__cause__ is not None:
            stack.append(curr.__cause__)
        if curr.__context__ is not None:
            stack.append(curr.__context__)
    return False


def _cause_token(exc: Exception) -> str:
    if isinstance(exc, MissingApiKeyError):
        return "key_missing"
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        if exc.response.status_code == 402:
            return "payment_required"
        if 500 <= exc.response.status_code <= 599:
            return f"provider_5xx({exc.response.status_code})"
    if _is_network_denied(exc):
        return "network_denied"
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return "unreachable"
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) is not None:
        return f"other({type(exc).__name__},{resp.status_code})"
    return f"other({type(exc).__name__})"


def _suggestion_for_cause(provider: EmbedderProviderConfig, exc: Exception) -> str:
    cause = _cause_token(exc)
    if cause == "key_missing":
        key_name = provider.api_key_env
        if not key_name and isinstance(exc, MissingApiKeyError):
            key_name = exc.name
        if not key_name:
            key_name = "DEEPINFRA_API_KEY"
        return (
            f"Store the key: scripts/axon-secrets.sh set {key_name} . "
            "A name that axon-secrets.sh list already prints means this process "
            "cannot decrypt the store."
        )
    if cause == "payment_required":
        return (
            "DeepInfra account is out of credit; top it up or point "
            "AXON_EMBEDDER_CHAIN at another provider."
        )
    if cause == "network_denied":
        return (
            "this process is sandboxed and cannot open the connection; "
            "run axon doctor outside the sandbox."
        )
    return (
        "the provider answered badly or not at all; retry, then check "
        "the provider's status page."
    )


def check_embedder_live(probe: ProbeFn | None = None) -> CheckResult:
    active_probe = probe or _probe
    chain = load_embedder_chain_config()
    recorded: list[tuple[EmbedderProviderConfig, Exception]] = []

    for provider in chain.providers:
        try:
            active_probe(provider, _PROBE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            recorded.append((provider, exc))
            continue
        return CheckResult(
            name="embedder.live",
            status=CheckStatus.OK,
            detail=f"{provider.name} served the probe",
        )

    if not recorded:
        return CheckResult(
            name="embedder.live",
            status=CheckStatus.FAIL,
            detail="no providers configured",
            suggestion="Configure AXON_EMBEDDER_CHAIN with at least one embedding provider.",
        )

    detail = "; ".join(f"{p.name}: {_cause_token(exc)}" for p, exc in recorded)
    key_providers = [(p, exc) for p, exc in recorded if p.api_key_env]
    target_provider, target_exc = key_providers[-1] if key_providers else recorded[-1]
    suggestion = _suggestion_for_cause(target_provider, target_exc)

    return CheckResult(
        name="embedder.live",
        status=CheckStatus.FAIL,
        detail=detail,
        suggestion=suggestion,
    )
