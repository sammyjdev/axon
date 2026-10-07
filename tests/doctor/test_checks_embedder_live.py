"""Tests for the live embedder doctor check."""

from __future__ import annotations

import errno
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from axon.config import runtime
from axon.config.runtime import EmbedderProviderConfig, load_embedder_chain_config
from axon.doctor import CheckStatus, run_all_checks
from axon.doctor.checks import embedder_live
from axon.doctor.checks.embedder_live import (
    _PROBE_TEXT,
    _PROBE_TIMEOUT_S,
    _probe,
    check_embedder_live,
)
from axon.embedder.providers import MissingApiKeyError

ProbeFn = Callable[[EmbedderProviderConfig, float], None]

_STORE_KEY_SUGGESTION = (
    "Store the key: scripts/axon-secrets.sh set DEEPINFRA_API_KEY . "
    "A name that axon-secrets.sh list already prints means this process "
    "cannot decrypt the store."
)


class _RecordingProbe:
    def __init__(self, causes: dict[str, Exception]) -> None:
        self.causes = causes
        self.calls: list[tuple[str, float]] = []

    def __call__(self, config: EmbedderProviderConfig, timeout: float) -> None:
        self.calls.append((config.name, timeout))
        if exc := self.causes.get(config.name):
            raise exc


def _raiser(exc: Exception) -> ProbeFn:
    def probe(_config: EmbedderProviderConfig, _timeout: float) -> None:
        raise exc

    return probe


def _server() -> ProbeFn:
    def probe(_config: EmbedderProviderConfig, _timeout: float) -> None:
        return None

    return probe


def _recording(causes: dict[str, Exception]) -> _RecordingProbe:
    return _RecordingProbe(causes)


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://example.invalid/v1/embeddings")
    return httpx.HTTPStatusError(
        "provider request failed",
        request=req,
        response=httpx.Response(status_code, request=req),
    )


def _network_denied() -> httpx.ConnectError:
    exc = httpx.ConnectError("blocked")
    exc.__cause__ = PermissionError(errno.EACCES, "Permission denied")
    return exc


class _OfflineViolation(BaseException):
    """The offline tripwire fired: a check tried a real HTTP request.

    Derives from BaseException on purpose: check_embedder_live's
    ``except Exception`` would swallow a plain AssertionError and hide a
    probe that went back online.
    """


def _six_distinct_causes() -> list[tuple[Exception, str]]:
    return [
        (MissingApiKeyError("DEEPINFRA_API_KEY"), "key_missing"),
        (_status_error(402), "payment_required"),
        (_status_error(503), "provider_5xx(503)"),
        (_network_denied(), "network_denied"),
        (httpx.ConnectError("refused"), "unreachable"),
        (ValueError("nonsense"), "other(ValueError)"),
    ]


@pytest.mark.parametrize(
    ("exc", "token"),
    [
        *_six_distinct_causes(),
        (_status_error(404), "other(HTTPStatusError,404)"),
        (_status_error(599), "provider_5xx(599)"),
        (_status_error(500), "provider_5xx(500)"),
    ],
)
def test_each_cause_gets_its_own_token(
    exc: Exception,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "deepinfra")

    result = check_embedder_live(probe=_raiser(exc))

    assert result.status is CheckStatus.FAIL
    assert result.name == "embedder.live"
    assert result.detail == f"deepinfra: {token}"


def test_no_two_causes_share_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "deepinfra")

    details = [
        check_embedder_live(probe=_raiser(exc)).detail
        for exc, _expected in _six_distinct_causes()
    ]
    tokens = [detail.split(": ", 1)[1] for detail in details]

    assert len(set(tokens)) == 6
    assert len({token.split("(", 1)[0] for token in tokens}) == 6


def test_probe_order_and_detail_follow_chain_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "ollama,deepinfra,nim")
    providers = load_embedder_chain_config().providers
    assert [provider.name for provider in providers] == ["ollama", "deepinfra", "nim"]
    probe = _recording(
        {
            "ollama": httpx.ConnectError("refused"),
            "deepinfra": MissingApiKeyError("DEEPINFRA_API_KEY"),
            "nim": _status_error(503),
        }
    )

    result = check_embedder_live(probe=probe)

    assert result.status is CheckStatus.FAIL
    assert probe.calls == [("ollama", 10.0), ("deepinfra", 10.0), ("nim", 10.0)]
    assert result.detail == "ollama: unreachable; deepinfra: key_missing; nim: provider_5xx(503)"
    assert (
        result.suggestion
        == "the provider answered badly or not at all; retry, then check "
        "the provider's status page."
    )


def test_suggestion_names_the_store_command_for_a_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _STORE_KEY_SUGGESTION
    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "deepinfra")

    result = check_embedder_live(
        probe=_raiser(MissingApiKeyError("DEEPINFRA_API_KEY"))
    )

    assert "scripts/axon-secrets.sh set DEEPINFRA_API_KEY" in result.suggestion
    assert "cannot decrypt the store" in result.suggestion
    assert result.suggestion == expected

    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "ollama,deepinfra")
    providers = load_embedder_chain_config().providers
    assert [provider.name for provider in providers] == ["ollama", "deepinfra"]
    probe = _recording(
        {
            "ollama": httpx.ConnectError("refused"),
            "deepinfra": MissingApiKeyError("DEEPINFRA_API_KEY"),
        }
    )

    result = check_embedder_live(probe=probe)

    assert result.suggestion == expected


def test_suggestion_targets_the_last_provider_with_an_api_key_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "deepinfra,ollama")
    providers = load_embedder_chain_config().providers
    assert [provider.name for provider in providers] == ["deepinfra", "ollama"]
    probe = _recording(
        {
            "deepinfra": MissingApiKeyError("DEEPINFRA_API_KEY"),
            "ollama": httpx.ConnectError("refused"),
        }
    )

    result = check_embedder_live(probe=probe)

    assert result.detail == "deepinfra: key_missing; ollama: unreachable"
    assert result.suggestion == _STORE_KEY_SUGGESTION


def test_suggestion_follows_the_last_keyed_provider_not_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "deepinfra,nim")
    providers = load_embedder_chain_config().providers
    assert [provider.name for provider in providers] == ["deepinfra", "nim"]
    probe = _recording(
        {
            "deepinfra": MissingApiKeyError("DEEPINFRA_API_KEY"),
            "nim": _status_error(503),
        }
    )

    result = check_embedder_live(probe=probe)

    assert result.status is CheckStatus.FAIL
    assert result.detail == "deepinfra: key_missing; nim: provider_5xx(503)"
    assert (
        result.suggestion
        == "the provider answered badly or not at all; retry, then check "
        "the provider's status page."
    )
    assert result.suggestion != _STORE_KEY_SUGGESTION

    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "nim,deepinfra")
    providers = load_embedder_chain_config().providers
    assert [provider.name for provider in providers] == ["nim", "deepinfra"]
    probe = _recording(
        {
            "nim": _status_error(503),
            "deepinfra": MissingApiKeyError("DEEPINFRA_API_KEY"),
        }
    )

    result = check_embedder_live(probe=probe)

    assert result.detail == "nim: provider_5xx(503); deepinfra: key_missing"
    assert result.suggestion == _STORE_KEY_SUGGESTION


def test_a_refused_local_provider_ahead_of_a_healthy_remote_is_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "ollama,deepinfra")
    providers = load_embedder_chain_config().providers
    assert [provider.name for provider in providers] == ["ollama", "deepinfra"]
    probe = _recording({"ollama": httpx.ConnectError("refused")})

    result = check_embedder_live(probe=probe)

    assert result.status is CheckStatus.OK
    assert result.status is not CheckStatus.WARN
    assert result.detail == "deepinfra served the probe"


def test_the_check_passes_the_ten_second_bound_to_the_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "deepinfra")
    probe = _recording({})

    result = check_embedder_live(probe=probe)

    assert result.status is CheckStatus.OK
    assert probe.calls == [("deepinfra", 10.0)]
    assert all(timeout == 10.0 for _name, timeout in probe.calls)
    assert _PROBE_TIMEOUT_S == 10.0


def test_the_probe_passes_its_bound_to_provider_fn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "deepinfra")
    config = load_embedder_chain_config().providers[0]
    assert config.name == "deepinfra"
    calls: list[tuple[EmbedderProviderConfig, float]] = []
    received_texts: list[list[str]] = []

    def provider(config: EmbedderProviderConfig, timeout: float):
        calls.append((config, timeout))

        def serve(texts: list[str]) -> list[list[float]]:
            received_texts.append(texts)
            return [[0.0]]

        return serve

    monkeypatch.setattr(embedder_live, "provider_fn", provider)

    _probe(config, 10.0)

    assert calls == [(config, 10.0)]
    assert received_texts == [[_PROBE_TEXT]]


def test_embedder_live_is_registered() -> None:
    by_name = {result.name: result for result in run_all_checks()}

    assert "embedder.live" in by_name
    assert by_name["embedder.live"].status is not CheckStatus.FAIL


def test_run_all_checks_stays_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise _OfflineViolation("run_all_checks attempted an HTTP request")

    monkeypatch.setattr(httpx, "post", boom)
    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "ollama")
    runtime._decoded_store.cache_clear()
    try:
        results = run_all_checks()
        misses = runtime._decoded_store.cache_info().misses
    finally:
        runtime._decoded_store.cache_clear()

    assert "embedder.live" in {result.name for result in results}
    assert misses == 0

    embedder = {result.name: result for result in results}["embedder.live"]
    assert embedder.status is CheckStatus.OK
    assert embedder.detail == "ollama served the probe"


def test_no_check_result_field_carries_a_store_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "credentials.cred"
    store.write_bytes(b"opaque blob")
    monkeypatch.setenv("AXON_CREDENTIALS_FILE", str(store))
    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "deepinfra")

    def decrypt(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="DEEPINFRA_API_KEY=stub-value-one\n",
            stderr="",
        )

    monkeypatch.setattr(runtime.subprocess, "run", decrypt)
    runtime._decoded_store.cache_clear()
    try:
        assert runtime.credential_from_store("DEEPINFRA_API_KEY") == "stub-value-one"
        result = check_embedder_live(
            probe=_raiser(MissingApiKeyError("DEEPINFRA_API_KEY"))
        )
    finally:
        runtime._decoded_store.cache_clear()

    rendered = result.detail + result.suggestion + result.name + str(result.status)
    assert "stub-value-one" not in rendered
