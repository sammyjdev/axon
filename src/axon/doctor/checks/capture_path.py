"""Capture-path checks for dec-114 / issue #141.

`axon health`/existing doctor checks cover the datastores (sqlite, pgvector,
vault, git) but not the capture path itself: a broken hook interpreter, a
stale pipx install, or a hook that never writes a decision all leave those
checks green while zero decisions get captured. These three checks probe the
capture path directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path
from subprocess import (
    TimeoutExpired,  # direct import: survives capture_path.subprocess being monkeypatched
)

from axon.config.runtime import load_runtime_config
from axon.doctor import CheckResult, CheckStatus
from axon.hooks.git_installer import _BEGIN, _END

_SKIP_NO_REPOS = "skipped: no onboarded repos"
_PROBE_TIMEOUT_S = 15
_GIT_TIMEOUT_S = 10


def _dev_root() -> Path:
    override = os.environ.get("AXON_DEV_ROOT")
    if override:
        return Path(override)
    return load_runtime_config().engine_root.parent


def _onboarded_repos(dev_root: Path) -> list[Path]:
    candidates: set[Path] = set()
    if dev_root.is_dir():
        # ponytail: two fixed-depth globs cover the observed `~/dev/<repo>` and
        # `~/dev/<group>/<repo>` layouts. A worktree's `.git` is a FILE, not a
        # directory, so it never matches `.git/hooks/post-commit` here - no
        # special-casing needed. A tree nested deeper than two levels would need
        # a real walk (rglob), not another fixed-depth glob.
        candidates.update(
            hook.parent.parent.parent.resolve()
            for pattern in ("*/.git/hooks/post-commit", "*/*/.git/hooks/post-commit")
            for hook in dev_root.glob(pattern)
        )

    registry_file = load_runtime_config().data_root / "onboarded_repos.json"
    if registry_file.is_file():
        try:
            raw_entries = json.loads(registry_file.read_text(encoding="utf-8"))
            if isinstance(raw_entries, list):
                for entry in raw_entries:
                    if isinstance(entry, str):
                        candidates.add(Path(entry).resolve())
        except (OSError, ValueError):
            pass

    repos = [repo for repo in candidates if _has_axon_hook(repo)]
    return sorted(repos, key=lambda repo: (repo.name, str(repo)))


def _repo_label(repo: Path, dev_root: Path) -> str:
    try:
        return str(repo.relative_to(dev_root.resolve()))
    except ValueError:
        return repo.name


def _has_axon_hook(repo: Path) -> bool:
    hook = repo / ".git" / "hooks" / "post-commit"
    if not hook.is_file():
        return False
    try:
        return _BEGIN in hook.read_text(encoding="utf-8")
    except OSError:
        return False


def _hook_interpreter(repo: Path) -> str | None:
    hook = repo / ".git" / "hooks" / "post-commit"
    text = hook.read_text(encoding="utf-8")
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == _BEGIN:
            in_block = True
            continue
        if stripped == _END:
            in_block = False
            continue
        if in_block and "-m axon.hooks.git_event" in line:
            tokens = shlex.split(line)
            if tokens:
                return tokens[0]
    return None


def check_hook_interpreters(*, dev_root: Path | None = None) -> CheckResult:
    resolved_dev_root = dev_root if dev_root is not None else _dev_root()
    repos = _onboarded_repos(resolved_dev_root)
    if not repos:
        return CheckResult(
            name="capture.hook_interpreter", status=CheckStatus.OK, detail=_SKIP_NO_REPOS
        )

    repos_by_interpreter: dict[str, list[str]] = {}
    malformed: list[str] = []
    for repo in repos:
        interp = _hook_interpreter(repo)
        if interp is None:
            malformed.append(repo.name)
            continue
        repos_by_interpreter.setdefault(interp, []).append(repo.name)

    broken: dict[str, str] = {}
    for interp in repos_by_interpreter:
        try:
            completed = subprocess.run(  # noqa: S603
                [interp, "-c", "import axon.hooks.git_event"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_S,
                check=False,
            )
        except (OSError, TimeoutExpired) as exc:
            broken[interp] = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            continue
        if completed.returncode != 0:
            stderr_first_line = completed.stderr.splitlines()[0] if completed.stderr else ""
            broken[interp] = stderr_first_line

    if broken:
        parts = []
        for interp, stderr_line in broken.items():
            names = ", ".join(repos_by_interpreter[interp])
            parts.append(f"{names}: interpreter {interp} broken ({stderr_line})")
        if malformed:
            parts.append(
                f"{', '.join(malformed)}: hook block malformed (no -m axon.hooks.git_event line)"
            )
        return CheckResult(
            name="capture.hook_interpreter",
            status=CheckStatus.FAIL,
            detail="; ".join(parts),
            suggestion=(
                "Re-run `axon install-hooks` in each affected repo to rebake the interpreter."
            ),
        )

    if malformed:
        probed = len(repos) - len(malformed)
        return CheckResult(
            name="capture.hook_interpreter",
            status=CheckStatus.OK,
            detail=(
                f"{probed} onboarded repo(s) healthy, "
                f"{len(malformed)} unparseable: {', '.join(malformed)}"
            ),
            suggestion=(
                "Re-run `axon install-hooks` in the affected repo(s) to rebake the hook block."
            ),
        )

    return CheckResult(
        name="capture.hook_interpreter",
        status=CheckStatus.OK,
        detail=(
            f"{len(repos)} onboarded repo(s), "
            f"{len(repos_by_interpreter)} interpreter(s) healthy"
        ),
    )


def _blob_sha1(data: bytes) -> str:
    return hashlib.sha1(  # noqa: S324
        b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
    ).hexdigest()


def check_install_freshness(
    *, engine_root: Path | None = None, installed_package: Path | None = None
) -> CheckResult:
    resolved_engine_root = (
        engine_root if engine_root is not None else load_runtime_config().engine_root
    ).resolve()
    if installed_package is not None:
        resolved_installed = installed_package.resolve()
    else:
        import axon

        resolved_installed = Path(axon.__file__).parent.resolve()

    if resolved_installed.is_relative_to(resolved_engine_root):
        return CheckResult(
            name="install.freshness",
            status=CheckStatus.OK,
            detail="skipped: running from the checkout",
        )

    ls_tree_argv = [
        "git",
        "-C",
        str(resolved_engine_root),
        "ls-tree",
        "-r",
        "HEAD",
        "--",
        "src/axon",
    ]
    try:
        completed = subprocess.run(  # noqa: S603
            ls_tree_argv,  # noqa: S607
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, TimeoutExpired) as exc:
        return CheckResult(
            name="install.freshness",
            status=CheckStatus.WARN,
            detail=f"skipped: git ls-tree failed ({exc})",
        )

    if completed.returncode != 0:
        return CheckResult(
            name="install.freshness",
            status=CheckStatus.WARN,
            detail=f"skipped: git ls-tree exited {completed.returncode}",
        )

    expected: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        meta, _, path = line.partition("\t")
        if not path or not path.endswith(".py"):
            continue
        sha = meta.split(" ")[2]
        relpath = path.removeprefix("src/axon/")
        expected[relpath] = sha

    if not expected:
        return CheckResult(
            name="install.freshness",
            status=CheckStatus.WARN,
            detail="skipped: no .py files found at HEAD under src/axon",
        )

    missing: list[str] = []
    stale: list[str] = []
    for relpath, expected_sha in expected.items():
        target = resolved_installed / relpath
        try:
            data = target.read_bytes()
        except OSError:
            missing.append(relpath)
            continue
        if _blob_sha1(data) != expected_sha:
            stale.append(relpath)

    if missing or stale:
        offending = (*missing, *stale)
        examples = ", ".join(offending[:5])
        if len(offending) > 5:
            examples += f", ... (+{len(offending) - 5} more)"
        return CheckResult(
            name="install.freshness",
            status=CheckStatus.FAIL,
            detail=f"{len(missing)} missing, {len(stale)} stale: {examples}",
            suggestion=(
                "Run `pipx install --force <engine_root>` to refresh the installed snapshot."
            ),
        )

    return CheckResult(
        name="install.freshness",
        status=CheckStatus.OK,
        detail=f"{len(expected)} files compared, install matches HEAD",
    )
