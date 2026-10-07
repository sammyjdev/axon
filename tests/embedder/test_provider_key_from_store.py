"""Provider keys are loaded lazily from the credential store without leaking."""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from axon.config import runtime
from axon.config.runtime import load_embedder_chain_config
from axon.embedder import providers


@pytest.fixture
def store_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    store = tmp_path / "credentials.cred"
    store.write_bytes(b"opaque blob")
    monkeypatch.setenv("AXON_CREDENTIALS_FILE", str(store))
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    runtime._decoded_store.cache_clear()
    yield store
    runtime._decoded_store.cache_clear()


def _decrypt_returns(monkeypatch: pytest.MonkeyPatch, stdout: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    return calls


def _fake_post(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    requests: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, list[dict[str, list[float]]]]:
            return {"data": [{"embedding": [3.0, 4.0]}]}

    def fake_post(_url: str, **kwargs: object) -> FakeResponse:
        requests.append(kwargs)
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    return requests


def test_embed_uses_the_store_key_when_the_variable_is_unset(
    store_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _decrypt_returns(monkeypatch, "DEEPINFRA_API_KEY=stub-value-one\n")
    requests = _fake_post(monkeypatch)
    deepinfra_cfg = load_embedder_chain_config().providers[-1]

    result = providers.embed_via_chain(
        ["probe"], providers=[providers.provider_fn(deepinfra_cfg)]
    )

    assert requests[0]["headers"] == {"Authorization": "Bearer stub-value-one"}
    assert result == [pytest.approx([0.6, 0.8])]


def test_a_name_absent_from_the_store_keeps_the_exact_message(
    store_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _decrypt_returns(monkeypatch, "OPENROUTER_API_KEY=stub-value-two\n")
    deepinfra_cfg = load_embedder_chain_config().providers[-1]

    with pytest.raises(RuntimeError) as exc:
        providers.provider_fn(deepinfra_cfg)(["probe"])

    assert str(exc.value) == "DEEPINFRA_API_KEY is not set"
    assert isinstance(exc.value, providers.MissingApiKeyError)


def test_one_decrypt_per_process_on_success(
    store_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _decrypt_returns(monkeypatch, "DEEPINFRA_API_KEY=stub-value-one\n")
    _fake_post(monkeypatch)
    deepinfra_cfg = load_embedder_chain_config().providers[-1]
    provider = providers.provider_fn(deepinfra_cfg)

    for _ in range(3):
        providers.embed_via_chain(["probe"], providers=[provider])

    assert len(calls) == 1


def test_one_decrypt_per_process_when_the_decrypt_fails(
    store_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def failing_run(cmd: list[str], **_kwargs: object) -> None:
        calls.append(cmd)
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(runtime.subprocess, "run", failing_run)
    deepinfra_cfg = load_embedder_chain_config().providers[-1]
    provider = providers.provider_fn(deepinfra_cfg)

    for _ in range(2):
        with pytest.raises(providers.MissingApiKeyError):
            provider(["probe"])

    assert len(calls) == 1


def test_an_exported_key_wins_and_no_decrypt_runs(
    store_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPINFRA_API_KEY", "exported-value-one")
    calls = _decrypt_returns(monkeypatch, "DEEPINFRA_API_KEY=stub-value-one\n")
    requests = _fake_post(monkeypatch)
    deepinfra_cfg = load_embedder_chain_config().providers[-1]

    providers.provider_fn(deepinfra_cfg)(["probe"])

    assert requests[0]["headers"] == {"Authorization": "Bearer exported-value-one"}
    assert calls == []


def test_no_store_file_means_no_decrypt_and_the_same_error(
    store_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_env.unlink()
    calls = _decrypt_returns(monkeypatch, "DEEPINFRA_API_KEY=stub-value-one\n")
    deepinfra_cfg = load_embedder_chain_config().providers[-1]

    with pytest.raises(RuntimeError) as exc:
        providers.provider_fn(deepinfra_cfg)(["probe"])

    assert str(exc.value) == "DEEPINFRA_API_KEY is not set"
    assert isinstance(exc.value, providers.MissingApiKeyError)
    assert calls == []


def test_the_store_never_reaches_the_environment(
    store_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _decrypt_returns(
        monkeypatch,
        "DEEPINFRA_API_KEY=stub-value-one\nOPENROUTER_API_KEY=stub-value-two\n",
    )
    _fake_post(monkeypatch)
    deepinfra_cfg = load_embedder_chain_config().providers[-1]

    providers.provider_fn(deepinfra_cfg)(["probe"])

    assert "DEEPINFRA_API_KEY" not in os.environ
    assert "OPENROUTER_API_KEY" not in os.environ


def test_no_log_record_carries_the_stub_value(
    store_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _decrypt_returns(monkeypatch, "DEEPINFRA_API_KEY=stub-value-one\n")

    def failing_post(_url: str, **_kwargs: object) -> None:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", failing_post)
    caplog.set_level(logging.DEBUG)
    deepinfra_cfg = load_embedder_chain_config().providers[-1]

    with pytest.raises(providers.AllProvidersFailedError) as exc:
        providers.embed_via_chain(
            ["probe"], providers=[providers.provider_fn(deepinfra_cfg)]
        )

    assert "stub-value-one" not in caplog.text
    assert "stub-value-one" not in str(exc.value)


def test_provider_fn_forwards_the_timeout_to_httpx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPINFRA_API_KEY", "exported-value-one")
    requests = _fake_post(monkeypatch)
    deepinfra_cfg = load_embedder_chain_config().providers[-1]

    providers.provider_fn(deepinfra_cfg, timeout=5.0)(["x"])
    providers.provider_fn(deepinfra_cfg)(["x"])

    assert [request["timeout"] for request in requests] == [5.0, 30.0]


@pytest.mark.parametrize("stored", ("stub-value-one ", "stub-value-one\t"))
def test_a_stored_key_is_sent_without_its_trailing_whitespace(
    store_env: Path, monkeypatch: pytest.MonkeyPatch, stored: str
) -> None:
    # httpx rejects a header value with trailing whitespace and quotes the whole
    # value in the error, which embed_via_chain then logs.
    _decrypt_returns(monkeypatch, f"DEEPINFRA_API_KEY={stored}\n")
    requests = _fake_post(monkeypatch)
    deepinfra_cfg = load_embedder_chain_config().providers[-1]

    providers.provider_fn(deepinfra_cfg)(["probe"])

    assert requests[0]["headers"] == {"Authorization": "Bearer stub-value-one"}


def test_an_exported_key_is_sent_without_its_trailing_whitespace(
    store_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPINFRA_API_KEY", "exported-value-one ")
    requests = _fake_post(monkeypatch)
    deepinfra_cfg = load_embedder_chain_config().providers[-1]

    providers.provider_fn(deepinfra_cfg)(["probe"])

    assert requests[0]["headers"] == {"Authorization": "Bearer exported-value-one"}


def test_a_whitespace_only_stored_key_counts_as_missing(
    store_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _decrypt_returns(monkeypatch, "DEEPINFRA_API_KEY=   \n")
    requests = _fake_post(monkeypatch)
    deepinfra_cfg = load_embedder_chain_config().providers[-1]

    with pytest.raises(providers.MissingApiKeyError):
        providers.provider_fn(deepinfra_cfg)(["probe"])

    assert requests == []
