"""Tests for the cross-agent AXON MCP tools (T6.1, T6.2)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from axon.mcp import server
from axon.store.session_store import SessionStore


@pytest.fixture
async def store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    monkeypatch.setattr(server, "_get_session_store", lambda: s)
    yield s
    await s.close()


async def test_axon_capture_creates_decision(store: SessionStore) -> None:
    response = await server.axon_capture(
        summary="adopt sqlite graph", repo="axon", agent="codex"
    )
    assert "captured dec-001" in response

    found = await store.find_decisions_by_repo("axon")
    assert len(found) == 1
    assert found[0].agent == "codex"
    assert found[0].summary == "adopt sqlite graph"


async def test_axon_capture_unknown_agent_falls_back_to_manual(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AXON_AGENT", raising=False)
    await server.axon_capture(summary="a decision", repo="axon")
    found = await store.find_decisions_by_repo("axon")
    assert found[0].agent == "manual"


async def test_axon_get_context_recalls_decisions(store: SessionStore) -> None:
    await server.axon_capture(summary="first decision", repo="axon")
    context = await server.axon_get_context(repo="axon")
    assert "first decision" in context


async def test_axon_search_matches_summary(store: SessionStore) -> None:
    await server.axon_capture(summary="drop neo4j backend", repo="axon")
    await server.axon_capture(summary="add redis cache", repo="axon")
    hits = await server.axon_search(query="neo4j", repo="axon")
    assert "drop neo4j backend" in hits
    assert "add redis cache" not in hits


async def test_axon_handoff_includes_context(store: SessionStore) -> None:
    await server.axon_capture(summary="a decision", repo="axon")
    brief = await server.axon_handoff(to_agent="codex", repo="axon")
    assert "handoff -> codex" in brief
    assert "a decision" in brief


async def test_axon_export_now_writes_docs(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_ALLOW_DESTRUCTIVE", "1")
    await server.axon_capture(summary="a decision", repo="axon")
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    monkeypatch.setattr(server, "discover_vault", lambda: vault)

    response = await server.axon_export_now(repo="axon")

    assert "exported 1 decision" in response
    assert (vault / "AXON" / "Architecture" / "axon.md").exists()


async def test_axon_mark_done_notes_and_exports(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_ALLOW_DESTRUCTIVE", "1")
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    monkeypatch.setattr(server, "discover_vault", lambda: vault)

    await server.axon_mark_done(repo="axon")

    notes = await store.get_notes("axon")
    assert any("marked done" in n.body for n in notes)


async def test_axon_export_now_denied_without_consent_env(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from axon.policy.core import PolicyDenied

    monkeypatch.delenv("AXON_ALLOW_DESTRUCTIVE", raising=False)
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    monkeypatch.setattr(server, "discover_vault", lambda: vault)

    with pytest.raises(PolicyDenied) as excinfo:
        await server.axon_export_now(repo="axon")

    assert excinfo.value.decision.reason_code.value == "DENY_DESTRUCTIVE_NO_CONSENT"


async def test_axon_health_reports_subsystems(store: SessionStore) -> None:
    report = await server.axon_health()
    assert report.startswith("# AXON health")
    for subsystem in ("sqlite", "pgvector", "vault", "git"):
        assert subsystem in report


async def test_axon_health_probes_git_in_vault_not_cwd(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: the git probe must check the vault, not the process cwd.

    The MCP server rarely runs from inside the vault, so probing the process
    working directory wrongly reported `git: not a repo` for a versioned vault.
    """
    import subprocess

    non_git_cwd = tmp_path / "cwd"
    non_git_cwd.mkdir()
    monkeypatch.chdir(non_git_cwd)

    vault_repo = tmp_path / "vault"
    vault_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=vault_repo, check=True)
    monkeypatch.setattr(server, "discover_vault", lambda: vault_repo)

    report = await server.axon_health()

    assert "git: ok" in report


async def test_axon_health_does_not_hang_when_backends_unreachable(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: axon_health must time-bound each external probe.

    When the vector store is unreachable (e.g. wrong host, offline VPN), the
    probe used to block indefinitely. Each probe must now fail fast with a
    timeout marker so `axon health` always returns within a few seconds.
    """
    import asyncio
    import time

    class _Hanging:
        async def ensure_collections(self) -> None:
            await asyncio.sleep(60)

    monkeypatch.setattr(server, "_get_vector_store", lambda: _Hanging())

    started = time.monotonic()
    report = await server.axon_health()
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"axon_health took {elapsed:.1f}s — must be time-bounded"
    assert "pgvector: down (timeout)" in report


def test_detect_agent_prefers_explicit_then_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert server._detect_agent("cursor") == "cursor"
    monkeypatch.setenv("AXON_AGENT", "codex")
    assert server._detect_agent() == "codex"
    monkeypatch.delenv("AXON_AGENT")
    assert server._detect_agent() == "unknown"
