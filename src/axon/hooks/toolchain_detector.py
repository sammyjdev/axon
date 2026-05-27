"""Hook toolchain detector (dec-113).

Looks at the repo to decide how AXON should integrate without competing
with the user's existing hook setup.

Detection rules (first match wins):

- ``.pre-commit-config.yaml`` exists → ``pre_commit_framework``
- ``.husky/`` directory or ``package.json#husky`` present → ``husky``
- ``.git/hooks/`` contains hooks managed by something other than AXON
  → ``custom``
- Otherwise → ``none``
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path


class Toolchain(StrEnum):
    PRE_COMMIT_FRAMEWORK = "pre_commit_framework"
    HUSKY = "husky"
    CUSTOM = "custom"
    NONE = "none"


def detect(repo_root: Path | None = None) -> Toolchain:
    root = repo_root or Path.cwd()

    if (root / ".pre-commit-config.yaml").exists() or (
        root / ".pre-commit-config.yml"
    ).exists():
        return Toolchain.PRE_COMMIT_FRAMEWORK

    if _husky_present(root):
        return Toolchain.HUSKY

    if _custom_hooks_present(root):
        return Toolchain.CUSTOM

    return Toolchain.NONE


def _husky_present(root: Path) -> bool:
    if (root / ".husky").is_dir():
        return True
    pkg = root / "package.json"
    if not pkg.exists():
        return False
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return "husky" in data


def _custom_hooks_present(root: Path) -> bool:
    """True if .git/hooks/ contains non-sample, non-AXON executables."""
    hooks_dir = root / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return False
    for entry in hooks_dir.iterdir():
        if entry.is_file() and not entry.name.endswith(".sample"):
            try:
                text = entry.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                # Binary or unreadable — assume custom user hook
                return True
            if "AXON git hook" not in text:
                return True
    return False
