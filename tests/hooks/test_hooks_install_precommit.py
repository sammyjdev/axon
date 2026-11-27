"""`axon hooks install --apply` in a pre-commit repo (issue #200).

Success means drivers installed, verified and the repo registered; any failure
exits 1 and leaves the repo and the registry byte for byte as they were.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from axon.cli.pb import app
from axon.config.runtime import load_runtime_config

runner = CliRunner()

_CFG = ".pre-commit-config.yaml"
_HOOK_TYPES = ("post-commit", "post-merge")


@pytest.fixture(autouse=True)
def _isolated_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No user or system git config (core.hooksPath), no user pre-commit cache.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("PRE_COMMIT_HOME", str(tmp_path / "pre-commit-home"))
    # The real pre-commit from `.[dev]`, also without an activated venv. A
    # missing binary fails these tests instead of skipping them.
    scripts = Path(sys.executable).parent
    monkeypatch.setenv("PATH", f"{scripts}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / _CFG).write_bytes(b"repos: []\n")
    return root


def _run(repo: Path, *args: str):
    with patch("os.isatty", return_value=True):
        return runner.invoke(app, ["hooks", "install", "--path", str(repo), *args])


def _registry_file() -> Path:
    return load_runtime_config().data_root / "onboarded_repos.json"


def _registered() -> list[str]:
    file = _registry_file()
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else []


def _hook(repo: Path, name: str) -> Path:
    return repo / ".git" / "hooks" / name


def _appendix_a() -> bytes:
    # `<py>` is shlex.quote(sys.executable); the backslash doubling is the YAML
    # double-quoted escape and changes nothing on a POSIX path.
    py = shlex.quote(sys.executable).replace("\\", "\\\\")
    return f"""repos:
  - repo: local
    hooks:
      - id: axon-post-commit
        name: AXON post-commit capture
        entry: "{py} -m axon.hooks.git_event commit"
        language: system
        stages: [post-commit]
        always_run: true
        pass_filenames: false
      - id: axon-post-merge
        name: AXON post-merge capture pulled signal commits
        entry: "{py} -m axon.hooks.git_event post-merge"
        language: system
        stages: [post-merge]
        always_run: true
        pass_filenames: false
""".encode()


def test_apply_installs_drivers_and_registers_repo(repo: Path) -> None:
    result = _run(repo, "--apply")

    assert result.exit_code == 0, result.output
    assert (
        "[axon] pre-commit drivers installed: post-commit, post-merge; repo registered"
        in result.stdout
    )
    assert (repo / _CFG).read_bytes() == _appendix_a()
    assert len(yaml.safe_load((repo / _CFG).read_bytes())["repos"]) == 1
    subprocess.run(["pre-commit", "validate-config", _CFG], cwd=repo, check=True)
    commit_driver = _hook(repo, "post-commit").read_text(encoding="utf-8")
    assert "--hook-type=post-commit" in commit_driver
    assert f"--config={_CFG}" in commit_driver
    assert "--hook-type=post-merge" in _hook(repo, "post-merge").read_text(encoding="utf-8")
    assert _registered() == [str(repo)]
