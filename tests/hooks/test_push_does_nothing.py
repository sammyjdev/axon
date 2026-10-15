"""A git push does nothing in AXON, and the commit hook loads no LLM client.

The pre-push hook judged drafts with an LLM and re-exported every decision to
the vault on every push: it counted "decisions since the last export" as the
repo's 20 latest decisions ever, a number that never resets, so any repo with
10 or more decisions ended its scope on every push. The judge it imported is
also what loaded litellm into every commit: 1.05 s of a 1.15 s import,
measured 2026-10-15.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from axon.hooks import git_event
from axon.hooks.git_installer import install_hooks

_OLD_PRE_PUSH = (
    "# >>> AXON git hook >>>\n"
    "# managed by `axon install-hooks` — failure never blocks git\n"
    "/usr/bin/python3 -m axon.hooks.git_event push 2>/dev/null || true\n"
    "# <<< AXON git hook <<<\n"
)


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)  # noqa: S603, S607
    return tmp_path


def test_installing_the_hooks_installs_no_pre_push(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    install_hooks(repo)

    assert not (repo / ".git" / "hooks" / "pre-push").exists()


def test_reinstalling_removes_an_old_axon_pre_push_that_held_nothing_else(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/usr/bin/env bash\n" + _OLD_PRE_PUSH, encoding="utf-8")

    install_hooks(repo)

    assert not hook.exists()


def test_reinstalling_keeps_the_operators_own_pre_push_lines(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/usr/bin/env bash\nmake lint\n\n" + _OLD_PRE_PUSH, encoding="utf-8")

    install_hooks(repo)

    assert hook.read_text(encoding="utf-8") == "#!/usr/bin/env bash\nmake lint\n"


def test_a_push_event_never_opens_the_decision_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    opened: list[str] = []

    def recording_store() -> object:
        opened.append("store")
        raise AssertionError("a push opened the decision store")

    monkeypatch.setattr(git_event, "_default_store", recording_store)

    assert git_event.main(["push"]) == 0
    assert opened == []


def test_importing_the_commit_hook_loads_no_llm_client() -> None:
    """An ordinary commit pays only this import. A commit carrying an
    architectural signal (arch:, decision:, ADR-Decision:) still runs ADR
    inference, which loads litellm; that stays until the owner decides on it."""
    loaded = subprocess.run(
        [sys.executable, "-c", "import sys, axon.hooks.git_event; print('litellm' in sys.modules)"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    assert loaded == "False"
