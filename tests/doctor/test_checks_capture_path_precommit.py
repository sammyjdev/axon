"""Doctor sees repos whose capture runs through pre-commit drivers (issue #200)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from axon.config.runtime import load_runtime_config
from axon.doctor import CheckStatus
from axon.doctor.checks import capture_path
from axon.doctor.checks.capture_path import _onboarded_repos, check_hook_interpreters

_CFG = ".pre-commit-config.yaml"
_YML = ".pre-commit-config.yml"
_DRIVER = "#!/usr/bin/env bash\nARGS=(hook-impl --config={cfg} --hook-type=post-commit)\n"


def _suggestion(cfg: str) -> str:
    return (
        "Fix the interpreter in the entry of axon-post-commit and axon-post-merge in "
        f"{cfg}, then run: pre-commit install --hook-type post-commit --hook-type post-merge"
    )


def _config(hook: str) -> bytes:
    return f"repos:\n  - repo: local\n    hooks:\n{hook}".encode()


_OK_HOOK = "      - id: axon-post-commit\n        entry: python -m axon.hooks.git_event commit\n"
_ENTRY_FIRST = (
    "      - entry: /nonexistent/python -m axon.hooks.git_event commit\n"
    "        id: axon-post-commit\n"
)
_DOUBLE_QUOTED = (
    "      - id: axon-post-commit\n"
    '        entry: "/nonexistent/python -m axon.hooks.git_event commit"\n'
)


@pytest.fixture
def dev_root(tmp_path: Path) -> Path:
    # Empty, so discovery goes through the registry alone.
    root = tmp_path / "dev"
    root.mkdir()
    return root


def _repo(
    tmp_path: Path,
    name: str,
    config: bytes,
    *,
    cfg_name: str = _CFG,
    driver: str = _DRIVER,
) -> Path:
    repo = (tmp_path / name).resolve()
    (repo / ".git" / "hooks").mkdir(parents=True)
    (repo / ".git" / "hooks" / "post-commit").write_text(driver.format(cfg=cfg_name))
    (repo / cfg_name).write_bytes(config)
    registry = load_runtime_config().data_root / "onboarded_repos.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    entries = json.loads(registry.read_text()) if registry.is_file() else []
    registry.write_text(json.dumps([*entries, str(repo)]))
    return repo


@pytest.mark.parametrize(
    ("cfg_name", "config", "driver", "included"),
    [
        (_CFG, _config(_OK_HOOK), _DRIVER, True),
        (_YML, _config(_OK_HOOK), _DRIVER, True),
        (_CFG, _config(_OK_HOOK), "#!/bin/sh\necho mine\n", False),
        (_CFG, b"repos: []\n", _DRIVER, False),
    ],
    ids=["yaml", "only-yml", "driver-without-hook-type", "config-without-axon"],
)
def test_precommit_repo_is_onboarded(
    tmp_path: Path,
    dev_root: Path,
    cfg_name: str,
    config: bytes,
    driver: str,
    included: bool,
) -> None:
    repo = _repo(tmp_path, "app", config, cfg_name=cfg_name, driver=driver)

    assert _onboarded_repos(dev_root) == ([repo] if included else [])


@pytest.mark.parametrize(
    ("hook", "cfg_name"),
    [(_ENTRY_FIRST, _CFG), (_DOUBLE_QUOTED, _YML)],
    ids=["entry-before-id", "double-quoted"],
)
def test_broken_precommit_interpreter_fails_with_precommit_suggestion(
    tmp_path: Path, dev_root: Path, hook: str, cfg_name: str
) -> None:
    repo = _repo(tmp_path, "broken-app", _config(hook), cfg_name=cfg_name)

    assert capture_path._hook_interpreter(repo) == "/nonexistent/python"
    result = check_hook_interpreters(dev_root=dev_root)

    assert result.status is CheckStatus.FAIL
    assert "broken-app" in result.detail
    assert result.suggestion == _suggestion(cfg_name)
    assert "axon install-hooks" not in result.suggestion


def test_broken_mix_shows_both_suggestions(tmp_path: Path, dev_root: Path) -> None:
    _repo(tmp_path, "framework-app", _config(_ENTRY_FIRST))
    plain = _repo(tmp_path, "plain-app", b"")
    (plain / _CFG).unlink()
    (plain / ".git" / "hooks" / "post-commit").write_text(
        "#!/usr/bin/env bash\n# >>> AXON git hook >>>\n"
        "/nonexistent/python -m axon.hooks.git_event commit\n# <<< AXON git hook <<<\n"
    )

    result = check_hook_interpreters(dev_root=dev_root)

    assert result.status is CheckStatus.FAIL
    assert _suggestion(_CFG) in result.suggestion
    assert "axon install-hooks" in result.suggestion


@pytest.mark.parametrize(
    "config",
    [
        b"repos: [\n# axon-post-commit\n",
        b"# \xff\n" + _config(_OK_HOOK),
        _config("      - id: axon-post-commit\n"),
    ],
    ids=["invalid-yaml", "non-utf8", "no-entry"],
)
def test_unparseable_precommit_config_is_reported_not_raised(
    tmp_path: Path, dev_root: Path, config: bytes
) -> None:
    repo = _repo(tmp_path, "odd-app", config)
    # A healthy neighbour, so the result is the OK branch that lists unparseables.
    _repo(
        tmp_path,
        "fine-app",
        _config(
            "      - id: axon-post-commit\n"
            f"        entry: \"'{sys.executable.replace(chr(92), '/')}' -m x\"\n"
        ),
    )

    assert capture_path._hook_interpreter(repo) is None
    result = check_hook_interpreters(dev_root=dev_root)

    assert "1 unparseable: odd-app" in result.detail
    assert result.suggestion == _suggestion(_CFG)
    assert "axon install-hooks" not in result.suggestion
