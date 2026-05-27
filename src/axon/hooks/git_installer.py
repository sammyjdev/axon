"""Install and remove AXON git hooks.

Each hook is a tiny shell snippet that calls ``python -m axon.hooks.git_event``.
Hook failures are swallowed so they never block a git operation.

Note: git has no ``post-push`` hook — the push event is captured by
``pre-push``. ``on_init`` is not a git hook; it is invoked by ``axon init``.
"""

from __future__ import annotations

import shlex
import stat
import sys
from pathlib import Path

from axon.exceptions import GitAnchorError

# git hook filename -> logical event passed to `axon.hooks.git_event`.
# post-merge / post-checkout were added by dec-111 to trigger L1-full
# revalidation of pending ADR drafts.
HOOKS: dict[str, str] = {
    "post-commit": "commit",
    "pre-push": "push",
    "post-merge": "post-merge",
    "post-checkout": "post-checkout",
}

_BEGIN = "# >>> AXON git hook >>>"
_END = "# <<< AXON git hook <<<"


def _block(event: str) -> str:
    # Bake in the interpreter that has `axon` installed (typically the pipx
    # venv). `command -v python` is unreliable on machines where the system
    # `python`/`python3` is a different env without the axon package.
    py = shlex.quote(sys.executable)
    return (
        f"{_BEGIN}\n"
        "# managed by `axon install-hooks` — failure never blocks git\n"
        f"{py} -m axon.hooks.git_event {event} 2>/dev/null || true\n"
        f"{_END}\n"
    )


def _hooks_dir(repo_path: Path | str) -> Path:
    repo = Path(repo_path)
    if not (repo / ".git").exists():
        raise GitAnchorError("not a git repository", repo=str(repo))
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    return hooks_dir


def _replace_block(text: str, new_block: str) -> str:
    """Replace the AXON-managed block in `text` with `new_block` in place."""
    out: list[str] = []
    skipping = False
    replaced = False
    for line in text.splitlines():
        if line.strip() == _BEGIN:
            skipping = True
            if not replaced:
                out.append(new_block.rstrip("\n"))
                replaced = True
            continue
        if line.strip() == _END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out) + "\n"


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
        desired_block = _block(event)
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if _BEGIN in existing:
                new_text = _replace_block(existing, desired_block)
                if new_text == existing:
                    continue  # already up to date
            else:
                new_text = existing.rstrip() + "\n\n" + desired_block
        else:
            new_text = "#!/usr/bin/env bash\n" + desired_block
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
