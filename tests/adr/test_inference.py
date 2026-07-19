"""Tests for axon.adr.inference orchestrator (dec-110/111/issue #15)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from axon.adr.inference import (
    InferenceStatus,
    run_for_head_async,
)


def _git(root: Path, *args: str) -> None:
    subprocess.check_call(
        ["git", *args], cwd=str(root),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tiny git repo with two commits — the second is HEAD."""
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / "data"))
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@x")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    (tmp_path / "a.py").write_text("x = 2\n")
    _git(tmp_path, "add", "a.py")
    return tmp_path


@pytest.mark.asyncio
class TestNoSignal:
    async def test_no_signal_short_circuits(self, fake_repo: Path) -> None:
        _git(fake_repo, "commit", "-q", "-m", "fix: typo")
        result = await run_for_head_async(
            project="t", repo_root=fake_repo
        )
        assert result.status is InferenceStatus.NO_SIGNAL


@pytest.mark.asyncio
class TestLLMUnavailable:
    async def test_llm_returns_none_marks_unavailable(self, fake_repo: Path) -> None:
        _git(fake_repo, "commit", "-q", "-m", "arch: refactor module layout")

        async def fake(*_a, **_kw):
            return None

        with patch("axon.adr.inference._call_llm", side_effect=fake):
            result = await run_for_head_async(
                project="t", repo_root=fake_repo
            )
        assert result.status is InferenceStatus.LLM_UNAVAILABLE


@pytest.mark.asyncio
class TestLLMNull:
    async def test_llm_returns_null_string(self, fake_repo: Path) -> None:
        _git(fake_repo, "commit", "-q", "-m", "arch: pointless rename")

        async def fake(*_a, **_kw):
            return "null"

        with patch("axon.adr.inference._call_llm", side_effect=fake):
            result = await run_for_head_async(
                project="t", repo_root=fake_repo
            )
        assert result.status is InferenceStatus.LLM_NULL


@pytest.mark.asyncio
class TestLLMParseError:
    async def test_invalid_json_caught(self, fake_repo: Path) -> None:
        _git(fake_repo, "commit", "-q", "-m", "arch: thing")

        async def fake(*_a, **_kw):
            return "{not valid"

        with patch("axon.adr.inference._call_llm", side_effect=fake):
            result = await run_for_head_async(
                project="t", repo_root=fake_repo
            )
        assert result.status is InferenceStatus.LLM_PARSE_ERROR


@pytest.mark.asyncio
class TestGateFailure:
    async def test_hallucinated_adr_goes_to_draft(self, fake_repo: Path) -> None:
        _git(fake_repo, "commit", "-q", "-m", "arch: tweak")

        async def fake(*_a, **_kw):
            import json
            return json.dumps({
                "title": "Quantum entanglement of storage",
                "context": "Cosmic radiation shielding required.",
                "decision": "Adopt neutrino flux primary storage.",
                "rationale": "Quantum entanglement guarantees state.",
            })

        with patch("axon.adr.inference._call_llm", side_effect=fake):
            result = await run_for_head_async(
                project="t", repo_root=fake_repo
            )
        assert result.status is InferenceStatus.GATE_FAILED
        assert result.outcome is not None
        assert not result.outcome.passed


@pytest.mark.asyncio
class TestSaveADR:
    async def test_legitimate_adr_persisted(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _git(
            fake_repo, "commit", "-q",
            "-m", "arch: introduce SessionRepository abstraction layer",
        )
        db_path = fake_repo / "axon.db"

        async def fake(*_a, **_kw):
            import json
            return json.dumps({
                "title": "Introduce repository pattern",
                "context": (
                    "Session storage was coupled to handlers — direct "
                    "imports of SessionRepository made testing hard."
                ),
                "decision": (
                    "Introduce SessionRepository as a contract layer "
                    "between persistence and request handlers."
                ),
                "rationale": (
                    "Adopt the repository pattern to decouple "
                    "persistence and isolate the abstraction boundary."
                ),
            })

        # Stub the whole gate pipeline (gate logic is tested separately
        # in tests/adr/gates/*). Here we only verify routing to
        # SessionStore on pass.
        from axon.adr.gates import GateOutcome

        with (
            patch("axon.adr.inference._call_llm", side_effect=fake),
            patch(
                "axon.adr.inference.evaluate",
                return_value=GateOutcome(passed=True),
            ),
        ):
            result = await run_for_head_async(
                project="t", repo_root=fake_repo
            )
        assert result.status is InferenceStatus.SAVED_ADR, result.error
        # Postgres-only (dec-121 Phase 3): verify the ADR actually persisted by
        # reading it back, not by checking for a SQLite file on disk.
        from axon.store.session_store import SessionStore

        store = SessionStore(db_path=db_path)
        await store.init()
        try:
            adrs = await store.get_adrs("t")
        finally:
            await store.close()
        assert len(adrs) >= 1
