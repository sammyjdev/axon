"""Tests for the evidence-based dec-129 repository re-key command."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import typer.main
from typer.testing import CliRunner

from axon.__main__ import app as cli_app
from axon.cli.pb import _get_db_path
from axon.core.decision import Decision
from axon.store.session_store import SessionStore

runner = CliRunner()


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)  # noqa: S603, S607


def _head(checkout: Path) -> str:
    return subprocess.check_output(  # noqa: S603, S607
        ["git", "-C", str(checkout), "log", "-1", "--pretty=%H"],  # noqa: S607
        text=True,
    ).strip()


def _decision(**overrides: object) -> Decision:
    base: dict[str, object] = {
        "id": "dec-001",
        "timestamp": datetime(2026, 8, 26, tzinfo=UTC),
        "agent": "manual",
        "repo": "agent-issue-154",
        "summary": "re-key this decision",
    }
    base.update(overrides)
    return Decision.model_validate(base)


def _save(*decisions: Decision) -> None:
    async def _run() -> None:
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            for decision in decisions:
                await store.save_decision(decision)
        finally:
            await store.close()

    asyncio.run(_run())


def _get(decision_id: str) -> Decision:
    async def _run() -> Decision:
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            return next(d for d in await store.all_decisions() if d.id == decision_id)
        finally:
            await store.close()

    return asyncio.run(_run())


def _count() -> int:
    async def _run() -> int:
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            return len(await store.all_decisions())
        finally:
            await store.close()

    return asyncio.run(_run())


def _main_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "myrepo"
    checkout.mkdir()
    _git(["init", "-b", "main"], checkout)
    _git(["config", "user.email", "test@axon.dev"], checkout)
    _git(["config", "user.name", "AXON Test"], checkout)
    (checkout / "main.py").write_text("print('hello')\n", encoding="utf-8")
    _git(["add", "."], checkout)
    _git(["commit", "-m", "feat: add the entry point"], checkout)
    return checkout


def test_rekey_repo_is_registered_on_the_entrypoint() -> None:
    cmd = typer.main.get_command(cli_app).commands["rekey-repo"]  # type: ignore[attr-defined]

    assert {p.name for p in cmd.params} >= {"checkout", "apply", "only_key", "all"}


def test_dry_run_reports_the_move_without_writing(tmp_path: Path) -> None:
    checkout = _main_checkout(tmp_path)
    _save(_decision(git_hash=_head(checkout)))

    result = runner.invoke(cli_app, ["rekey-repo", str(checkout)])

    assert result.exit_code == 0, result.output
    assert "would re-key" in result.output
    assert "dec-001" in result.output
    assert _get("dec-001").repo == "agent-issue-154"


def test_apply_moves_only_provably_reachable_rows(tmp_path: Path) -> None:
    checkout = _main_checkout(tmp_path)
    _save(
        _decision(git_hash=_head(checkout)),
        _decision(id="dec-002", repo="agent-issue-999", git_hash="0" * 40),
    )

    result = runner.invoke(cli_app, ["rekey-repo", str(checkout), "--apply", "--all"])

    assert result.exit_code == 0, result.output
    assert _get("dec-001").repo == "myrepo"
    assert _get("dec-002").repo == "agent-issue-999"
    assert _count() == 2


def test_apply_preserves_judged_and_validation_score(tmp_path: Path) -> None:
    checkout = _main_checkout(tmp_path)
    _save(_decision(git_hash=_head(checkout), judged=True, validation_score=3.5))

    result = runner.invoke(cli_app, ["rekey-repo", str(checkout), "--apply", "--all"])

    assert result.exit_code == 0, result.output
    decision = _get("dec-001")
    assert decision.judged is True
    assert decision.validation_score == 3.5


def test_apply_without_a_key_filter_refuses(tmp_path: Path) -> None:
    checkout = _main_checkout(tmp_path)
    _save(_decision(git_hash=_head(checkout)))

    result = runner.invoke(cli_app, ["rekey-repo", str(checkout), "--apply"])

    assert result.exit_code != 0
    assert "--only-key or --all" in result.output
    assert _get("dec-001").repo == "agent-issue-154"


def test_only_key_preserves_a_legitimately_keyed_row(tmp_path: Path) -> None:
    checkout = _main_checkout(tmp_path)
    git_hash = _head(checkout)
    _save(
        _decision(git_hash=git_hash),
        _decision(id="dec-002", repo="glyph-kg", git_hash=git_hash),
    )

    result = runner.invoke(
        cli_app,
        ["rekey-repo", str(checkout), "--apply", "--only-key", "agent-*"],
    )

    assert result.exit_code == 0, result.output
    assert _get("dec-001").repo == "myrepo"
    assert _get("dec-002").repo == "glyph-kg"


def test_all_moves_every_provable_row(tmp_path: Path) -> None:
    checkout = _main_checkout(tmp_path)
    git_hash = _head(checkout)
    _save(
        _decision(git_hash=git_hash),
        _decision(id="dec-002", repo="glyph-kg", git_hash=git_hash),
    )

    result = runner.invoke(cli_app, ["rekey-repo", str(checkout), "--apply", "--all"])

    assert result.exit_code == 0, result.output
    assert _get("dec-001").repo == "myrepo"
    assert _get("dec-002").repo == "myrepo"
