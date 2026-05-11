from __future__ import annotations

from prometheus.cli.setup_session import SetupSession


def test_defaults():
    s = SetupSession()
    assert s.transport == "stdio"
    assert s.http_host is None
    assert s.http_port is None
    assert s.languages == ()
    assert s.profile == "solo-dev"
    assert s.vault_contexts == ()
    assert s.include_work_context is False


def test_with_http_transport():
    s = SetupSession(transport="http", http_port=8080)
    assert s.transport == "http"
    assert s.http_port == 8080
    assert s.http_host is None


def test_with_languages_and_vault():
    s = SetupSession(
        languages=("python", "typescript"),
        vault_contexts=("personal", "knowledge"),
        include_work_context=True,
    )
    assert "python" in s.languages
    assert "personal" in s.vault_contexts
    assert s.include_work_context is True
