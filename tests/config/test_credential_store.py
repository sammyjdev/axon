"""The engine fills missing env vars from the systemd-creds store that
``scripts/axon-secrets.sh`` writes, so keys reach the MCP server and the git
hooks without per-rail wiring."""

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
    return fake_env


def _decrypt_returns(monkeypatch: pytest.MonkeyPatch, stdout: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    return calls


def test_fills_missing_keys_and_keeps_exported_ones(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env["OPENROUTER_API_KEY"] = "from-shell"
    calls = _decrypt_returns(
        monkeypatch, "DEEPINFRA_API_KEY=abc=def\nOPENROUTER_API_KEY=from-store\n"
    )

    runtime._load_credential_store()

    assert env["DEEPINFRA_API_KEY"] == "abc=def"
    assert env["OPENROUTER_API_KEY"] == "from-shell"
    assert calls == [
        ["systemd-creds", "--user", "decrypt", "--name=axon", env["AXON_CREDENTIALS_FILE"], "-"]
    ]


def test_no_store_means_no_decrypt(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    Path(env["AXON_CREDENTIALS_FILE"]).unlink()
    calls = _decrypt_returns(monkeypatch, "DEEPINFRA_API_KEY=never\n")

    runtime._load_credential_store()

    assert calls == []
    assert "DEEPINFRA_API_KEY" not in env


def test_failed_decrypt_leaves_the_environment_alone(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_run(cmd: list[str], **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(runtime.subprocess, "run", failing_run)
    before = dict(env)

    runtime._load_credential_store()

    assert env == before


def test_suite_never_points_at_an_operator_store() -> None:
    assert not Path(os.environ["AXON_CREDENTIALS_FILE"]).is_file()
