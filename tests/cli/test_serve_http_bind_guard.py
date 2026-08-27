from __future__ import annotations

import sys
import types
from typing import Any

import pytest
import typer.main
from typer.testing import CliRunner

from axon.__main__ import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_ambient_http_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub ambient auth env before each test to prevent shell env leakage.

    Without it, a developer who exports AXON_HTTP_TOKEN in their shell turns the
    'without token' tests green-for-the-wrong-reason or red. tests/http already
    has this via its conftest; tests/cli does not.
    """
    monkeypatch.delenv("AXON_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("AXON_HTTP_ALLOW_UNAUTHENTICATED", raising=False)


def _spy_uvicorn(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    fake_uvicorn = types.SimpleNamespace(run=lambda *a, **kw: calls.append(kw))
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    return calls


def _get_output(result: Any) -> str:
    out = result.output or ""
    err = getattr(result, "stderr", "") or ""
    return out + err


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",  # noqa: S104
        "192.168.1.10",
        "100.78.123.92",
        "1270.0.0.1",
        "evaluator.internal",
        "127.example.com",  # Discrimination sensor against a startswith("127.") implementation
        "127.0.0.1.evil.com",  # Discrimination sensor against a startswith("127.") implementation
    ],
)
def test_non_loopback_without_token_refuses_and_never_starts_the_server(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    calls = _spy_uvicorn(monkeypatch)
    result = runner.invoke(app, ["serve-http", "--host", host])

    assert result.exit_code == 1
    assert calls == []
    assert "AXON_HTTP_TOKEN" in _get_output(result)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "::1",
        "localhost",
        "127.0.0.53",
    ],
)
def test_loopback_hosts_start_the_server(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    calls = _spy_uvicorn(monkeypatch)
    result = runner.invoke(app, ["serve-http", "--host", host])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["host"] == host


def test_non_loopback_with_a_token_starts_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AXON_HTTP_TOKEN", "valid-secret-token")  # noqa: S105
    calls = _spy_uvicorn(monkeypatch)
    result = runner.invoke(app, ["serve-http", "--host", "0.0.0.0"])  # noqa: S104

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["host"] == "0.0.0.0"  # noqa: S104


def test_empty_token_does_not_satisfy_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AXON_HTTP_TOKEN", "")  # noqa: S105
    calls = _spy_uvicorn(monkeypatch)
    result = runner.invoke(app, ["serve-http", "--host", "0.0.0.0"])  # noqa: S104

    assert result.exit_code == 1
    assert calls == []


def test_allow_unauthenticated_flag_permits_a_wide_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _spy_uvicorn(monkeypatch)
    result = runner.invoke(app, ["serve-http", "--host", "0.0.0.0", "--allow-unauthenticated"])  # noqa: S104

    assert result.exit_code == 0
    assert len(calls) == 1


def test_allow_unauthenticated_env_permits_a_wide_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AXON_HTTP_ALLOW_UNAUTHENTICATED", "1")
    calls = _spy_uvicorn(monkeypatch)
    result = runner.invoke(app, ["serve-http", "--host", "0.0.0.0"])  # noqa: S104

    assert result.exit_code == 0
    assert len(calls) == 1


@pytest.mark.parametrize("val", ["0", "", "false", "no"])
def test_falsy_allow_unauthenticated_env_still_refuses(
    monkeypatch: pytest.MonkeyPatch, val: str
) -> None:
    monkeypatch.setenv("AXON_HTTP_ALLOW_UNAUTHENTICATED", val)
    calls = _spy_uvicorn(monkeypatch)
    result = runner.invoke(app, ["serve-http", "--host", "0.0.0.0"])  # noqa: S104

    assert result.exit_code == 1
    assert calls == []


def test_the_guard_runs_before_the_uvicorn_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    result = runner.invoke(app, ["serve-http", "--host", "0.0.0.0"])  # noqa: S104

    output = _get_output(result)
    assert result.exit_code == 1
    assert "AXON_HTTP_TOKEN" in output
    assert "uvicorn is not installed" not in output


def test_serve_http_exposes_allow_unauthenticated_on_the_entrypoint_app() -> None:
    cmd = typer.main.get_command(app).commands["serve-http"]  # type: ignore[attr-defined]
    assert {p.name for p in cmd.params} >= {"port", "host", "reload", "allow_unauthenticated"}


def test_serve_http_never_echoes_the_token_on_the_path_that_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not the refusal path - a token being set is what DISABLES the refusal.

    The earlier name claimed this covered the refusal message, which it cannot:
    the guard only refuses when no token is set, so on the refusal path there is
    no token to leak. What is worth asserting is that the path which does start
    the server, with a token in the environment, keeps it out of every stream.
    """
    token_val = "SECRET_TOKEN_XYZ_123"  # noqa: S105
    monkeypatch.setenv("AXON_HTTP_TOKEN", token_val)
    spy = _spy_uvicorn(monkeypatch)
    result = runner.invoke(app, ["serve-http", "--host", "0.0.0.0"])  # noqa: S104

    assert spy, "server must actually have started, or this asserts nothing"
    output = _get_output(result)
    assert token_val not in output
    assert token_val not in result.output
    assert token_val not in getattr(result, "stderr", "")
