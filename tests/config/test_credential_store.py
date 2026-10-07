"""The engine returns one requested name from the systemd-creds store that
``scripts/axon-secrets.sh`` writes, so the embedding path gets its key without
per-rail wiring and without the store reaching ``os.environ``."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from axon.config import runtime


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A private environment holding only a store path, swapped in for os.environ."""
    store = tmp_path / "credentials.cred"
    store.write_bytes(b"opaque blob")
    fake_env = {"AXON_CREDENTIALS_FILE": str(store)}
    monkeypatch.setattr(runtime.os, "environ", fake_env)
    runtime._decoded_store.cache_clear()
    yield fake_env
    runtime._decoded_store.cache_clear()


def _decrypt_returns(monkeypatch: pytest.MonkeyPatch, stdout: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    return calls


def test_returns_only_the_requested_name_and_never_the_environment(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env["OPENROUTER_API_KEY"] = "from-shell"
    calls = _decrypt_returns(
        monkeypatch, "DEEPINFRA_API_KEY=abc=def\nOPENROUTER_API_KEY=from-store\n"
    )

    assert runtime.credential_from_store("DEEPINFRA_API_KEY") == "abc=def"
    assert runtime.credential_from_store("OPENROUTER_API_KEY") == "from-store"
    assert env["OPENROUTER_API_KEY"] == "from-shell"
    assert "DEEPINFRA_API_KEY" not in env
    assert calls == [
        ["systemd-creds", "--user", "decrypt", "--name=axon", env["AXON_CREDENTIALS_FILE"], "-"]
    ]


def test_no_store_means_no_decrypt(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    Path(env["AXON_CREDENTIALS_FILE"]).unlink()
    calls = _decrypt_returns(monkeypatch, "DEEPINFRA_API_KEY=never\n")

    assert runtime.credential_from_store("DEEPINFRA_API_KEY") is None

    assert calls == []
    assert "DEEPINFRA_API_KEY" not in env


def test_failed_decrypt_returns_nothing(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def failing_run(cmd: list[str], **_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(runtime.subprocess, "run", failing_run)
    before = dict(env)

    assert runtime.credential_from_store("DEEPINFRA_API_KEY") is None

    assert env == before
    assert runtime.credential_from_store("DEEPINFRA_API_KEY") is None
    assert calls == 1


def test_suite_never_points_at_an_operator_store() -> None:
    assert not Path(os.environ["AXON_CREDENTIALS_FILE"]).is_file()
