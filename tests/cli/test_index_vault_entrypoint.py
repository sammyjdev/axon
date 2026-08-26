"""Issue #147: `axon index-vault` must be wired on the installed entry point.

`index_vault` lives in `axon.cli.pb` and is registered on pb's own Typer app,
but the installed binary is `axon.__main__:app`, a SEPARATE Typer app that
re-registers pb's commands explicitly. Until issue #147 the function was
never imported there, so `axon index-vault` answered "No such command" while
every existing test (tests/cli/test_pb_cli.py) drove `pb.app` and stayed
green - the same gap that cost this repo issues #142 and #145.

Every test here drives `axon.__main__:app`, NEVER `pb.app`. And per RULES.md
(FORGE #60) a registration can be silently miswired to the wrong underlying
function (its sibling `index-dev` also owns a --dry-run option), so a
name-registration check alone is not enough: the behavioral test asserts on
output unique to `index_vault` itself.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest
import typer.main
from typer.testing import CliRunner

from axon.__main__ import app as main_app
from axon.cli import pb

runner = CliRunner()


def test_entrypoint_registers_index_vault_with_dry_run_option() -> None:
    """Structural wiring: the command exists on the entry-point app.

    Option names come from Click's parameter list, never from rendered help
    text - terminal width and the resolved Rich version make `--help` output
    unstable (the established idiom in tests/cli/test_seed_lessons_cli.py).
    """
    main_command = typer.main.get_command(main_app)
    index_vault_command = main_command.commands["index-vault"]  # type: ignore[attr-defined]

    dry_run_option = next(
        (p for p in index_vault_command.params if "--dry-run" in p.opts),
        None,
    )

    assert dry_run_option is not None, "index-vault must accept --dry-run"


def test_index_vault_dry_run_through_entrypoint_lists_vault_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavioral wiring (FORGE #60): invoke `index-vault` through the
    entry-point app against an isolated temp vault.

    Only `index_vault` echoes `vault_root: <path>` and `files=<n>` - a
    registration miswired to another --dry-run command (e.g. index-dev)
    fails here even though it passes the structural test. --dry-run writes
    nothing and opens no DB/network connection, so this is hermetic.
    """
    vault_root = tmp_path / "vault"
    knowledge_dir = vault_root / "knowledge" / "research" / "some-slug"
    career_dir = vault_root / "career"
    ignored_dir = vault_root / "knowledge" / "node_modules"
    knowledge_dir.mkdir(parents=True)
    career_dir.mkdir(parents=True)
    ignored_dir.mkdir(parents=True)
    (knowledge_dir / "CONTEXT.md").write_text("# Context\n", encoding="utf-8")
    (career_dir / "resume.md").write_text("# Resume\n", encoding="utf-8")
    (ignored_dir / "readme.md").write_text("# Ignored\n", encoding="utf-8")

    monkeypatch.setattr(pb, "_RUNTIME", types.SimpleNamespace(vault_root=vault_root))

    result = runner.invoke(main_app, ["index-vault", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert f"vault_root: {vault_root}" in result.stdout, result.output
    assert "files=2" in result.stdout, result.output
