"""Install and remove AXON git hooks.

Each hook is a tiny shell snippet that calls ``python -m axon.hooks.git_event``.
Hook failures are swallowed so they never block a git operation.

Note: git has no ``post-push`` hook — the push event is captured by
``pre-push``. ``on_init`` is not a git hook; it is invoked by ``axon init``.
"""

from __future__ import annotations

import stat
from pathlib import Path

from axon.exceptions import GitAnchorError

# git hook filename -> logical event passed to `axon.hooks.git_event`
HOOKS: dict[str, str] = {"post-commit": "commit", "pre-push": "push"}

_BEGIN = "# >>> AXON git hook >>>"
_END = "# <<< AXON git hook <<<"


def _block(event: str) -> str:
    return (
        f"{_BEGIN}\n"
        "# managed by `axon install-hooks` — failure never blocks git\n"
        f"python -m axon.hooks.git_event {event} 2>/dev/null || true\n"
        f"{_END}\n"
    )


def _hooks_dir(repo_path: Path | str) -> Path:
    repo = Path(repo_path)
    if not (repo / ".git").exists():
        raise GitAnchorError("not a git repository", repo=str(repo))
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    return hooks_dir


def _strip_block(text: str) -> str:
    """Remove the AXON-managed block from a hook file's text."""
    out: list[str] = []
    skipping = False
    for line in text.splitlines():
        if line.strip() == _BEGIN:
            skipping = True
            continue
        if line.strip() == _END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    body = "\n".join(out).rstrip()
    return body + "\n" if body else ""


def install_hooks(repo_path: Path | str = ".") -> list[str]:
    """Install AXON git hooks. Idempotent — already-installed hooks are skipped.

    Returns the hook names that were newly installed.
    """
    hooks_dir = _hooks_dir(repo_path)
    installed: list[str] = []
    for hook_name, event in HOOKS.items():
        target = hooks_dir / hook_name
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if _BEGIN in existing:
                continue  # already installed
            new_text = existing.rstrip() + "\n\n" + _block(event)
        else:
            new_text = "#!/usr/bin/env bash\n" + _block(event)
        target.write_text(new_text, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        installed.append(hook_name)
    return installed


def uninstall_hooks(repo_path: Path | str = ".") -> list[str]:
    """Remove AXON-managed hook blocks. Returns the hook names touched."""
    hooks_dir = _hooks_dir(repo_path)
    removed: list[str] = []
    for hook_name in HOOKS:
        target = hooks_dir / hook_name
        if not target.exists():
            continue
        existing = target.read_text(encoding="utf-8")
        if _BEGIN not in existing:
            continue
        stripped = _strip_block(existing)
        if stripped.strip() in ("", "#!/usr/bin/env bash"):
            target.unlink()
        else:
            target.write_text(stripped, encoding="utf-8")
        removed.append(hook_name)
    return removed


def hooks_installed(repo_path: Path | str = ".") -> bool:
    """True if every AXON hook is installed in the repo."""
    hooks_dir = Path(repo_path) / ".git" / "hooks"
    if not hooks_dir.exists():
        return False
    return all(
        (hooks_dir / name).exists() and _BEGIN in (hooks_dir / name).read_text("utf-8")
        for name in HOOKS
    )
