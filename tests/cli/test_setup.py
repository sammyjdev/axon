from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from prometheus.cli.setup import format_next_steps, run_step_commit, run_step_domain, run_step_transport, run_step_vault
from prometheus.cli.setup_session import SetupSession


def test_transport_stdio(monkeypatch):
    monkeypatch.setattr("typer.prompt", lambda *a, **kw: "1")
    session = run_step_transport(SetupSession())
    assert session.transport == "stdio"
    assert session.http_port is None
    assert session.http_host is None


def test_transport_http_local(monkeypatch):
    responses = iter(["2", "8080"])
    monkeypatch.setattr("typer.prompt", lambda *a, **kw: next(responses))
    session = run_step_transport(SetupSession())
    assert session.transport == "http"
    assert session.http_port == 8080
    assert session.http_host is None


def test_transport_http_server(monkeypatch):
    responses = iter(["3", "0.0.0.0", "9000"])
    monkeypatch.setattr("typer.prompt", lambda *a, **kw: next(responses))
    session = run_step_transport(SetupSession())
    assert session.transport == "http"
    assert session.http_host == "0.0.0.0"
    assert session.http_port == 9000


def test_transport_http_local_invalid_port_retries(monkeypatch):
    responses = iter(["2", "abc", "8080"])
    monkeypatch.setattr("typer.prompt", lambda *a, **kw: next(responses))
    session = run_step_transport(SetupSession())
    assert session.transport == "http"
    assert session.http_port == 8080


def test_domain_single_language(monkeypatch):
    responses = iter(["1", "1"])  # Python, solo-dev
    monkeypatch.setattr("typer.prompt", lambda *a, **kw: next(responses))
    session = run_step_domain(SetupSession())
    assert "python" in session.languages
    assert session.profile == "solo-dev"


def test_domain_multiple_languages(monkeypatch):
    responses = iter(["1,2", "2"])  # Python + Kotlin, team-dev
    monkeypatch.setattr("typer.prompt", lambda *a, **kw: next(responses))
    session = run_step_domain(SetupSession())
    assert "python" in session.languages
    assert "kotlin" in session.languages
    assert session.profile == "team-dev"


def test_domain_other_language(monkeypatch):
    responses = iter(["6", "1"])  # Other, solo-dev
    monkeypatch.setattr("typer.prompt", lambda *a, **kw: next(responses))
    session = run_step_domain(SetupSession())
    assert session.languages == ()
    assert session.profile == "solo-dev"


def test_domain_privacy_profile(monkeypatch):
    responses = iter(["1", "3"])  # Python, privacy-first
    monkeypatch.setattr("typer.prompt", lambda *a, **kw: next(responses))
    session = run_step_domain(SetupSession())
    assert session.profile == "privacy-first"


def test_vault_default_contexts(monkeypatch):
    responses = iter(["1,2", "2"])  # personal+knowledge, no work
    monkeypatch.setattr("typer.prompt", lambda *a, **kw: next(responses))
    session = run_step_vault(SetupSession())
    assert "personal" in session.vault_contexts
    assert "knowledge" in session.vault_contexts
    assert "career" not in session.vault_contexts
    assert session.include_work_context is False


def test_vault_with_work_context(monkeypatch):
    responses = iter(["1,2,3", "1"])  # personal+knowledge+career, yes work
    monkeypatch.setattr("typer.prompt", lambda *a, **kw: next(responses))
    session = run_step_vault(SetupSession())
    assert "career" in session.vault_contexts
    assert session.include_work_context is True


def test_vault_all_contexts_no_work(monkeypatch):
    responses = iter(["1,2,3,4", "2"])  # all four, no work
    monkeypatch.setattr("typer.prompt", lambda *a, **kw: next(responses))
    session = run_step_vault(SetupSession())
    assert set(session.vault_contexts) == {"personal", "knowledge", "career", "saas"}
    assert session.include_work_context is False


def test_commit_writes_mcp_section(tmp_path):
    config_path = tmp_path / "prometheus.toml"
    config_path.write_text("[runtime]\nmode = \"full-local\"\n", encoding="utf-8")
    vault_root = tmp_path / "vault"

    session = SetupSession(transport="http", http_port=8080, vault_contexts=("personal",))
    messages = run_step_commit(session, config_path=config_path, vault_root=vault_root)

    content = config_path.read_text()
    assert "[mcp]" in content
    assert 'transport = "http"' in content
    assert "port = 8080" in content
    assert any("mcp" in m.lower() or "config" in m.lower() for m in messages)


def test_commit_scaffolds_vault_dirs(tmp_path):
    config_path = tmp_path / "prometheus.toml"
    config_path.write_text("[runtime]\nmode = \"full-local\"\n", encoding="utf-8")
    vault_root = tmp_path / "vault"

    session = SetupSession(
        vault_contexts=("personal", "knowledge"),
        include_work_context=True,
    )
    run_step_commit(session, config_path=config_path, vault_root=vault_root)

    assert (vault_root / "personal").is_dir()
    assert (vault_root / "knowledge").is_dir()
    assert (vault_root / "work").is_dir()
    assert (vault_root / "personal" / ".gitkeep").exists()
    assert (vault_root / "work" / "README.md").exists()


def test_commit_validates_domain_packs(tmp_path):
    config_path = tmp_path / "prometheus.toml"
    config_path.write_text("[runtime]\nmode = \"full-local\"\n", encoding="utf-8")
    vault_root = tmp_path / "vault"

    packs_root = tmp_path / "domain-packs"
    packs_root.mkdir()
    (packs_root / "python.json").write_text(
        '{"schema_version":"1","domain_id":"python","display_name":"Python",'
        '"description":"Python","signals":{"languages":["python"],'
        '"artifact_types":[],"task_types":[]}}',
        encoding="utf-8",
    )

    session = SetupSession(languages=("python",))
    messages = run_step_commit(
        session,
        config_path=config_path,
        vault_root=vault_root,
        packs_root=packs_root,
    )
    assert any("python" in m.lower() for m in messages)


def test_commit_returns_messages_list(tmp_path):
    config_path = tmp_path / "prometheus.toml"
    config_path.write_text("[runtime]\nmode = \"full-local\"\n", encoding="utf-8")
    vault_root = tmp_path / "vault"

    session = SetupSession()
    messages = run_step_commit(session, config_path=config_path, vault_root=vault_root)
    assert isinstance(messages, list)
    assert len(messages) > 0


def test_next_steps_stdio():
    session = SetupSession(transport="stdio")
    text = format_next_steps(session)
    assert "mcpServers" in text
    assert "pb" in text
    assert "pb index" in text


def test_next_steps_http_local():
    session = SetupSession(transport="http", http_port=8080)
    text = format_next_steps(session)
    assert "8080" in text
    assert "ngrok" in text
    assert "pb index" in text


def test_next_steps_http_server():
    session = SetupSession(transport="http", http_host="0.0.0.0", http_port=9000)
    text = format_next_steps(session)
    assert "9000" in text
    assert "reverse proxy" in text
    assert "pb index" in text
