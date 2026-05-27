"""Toolchain compatibility checks for dec-114.

Warns when the user's commit-msg linting toolchain (commitlint /
semantic-release) rejects the ``arch:`` / ``decision:`` prefix that
dec-110 uses to mark architectural commits.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from axon.doctor import CheckResult, CheckStatus

_TYPE_ENUM_KEY = re.compile(r"['\"]type-enum['\"]\s*:")
_TYPES_LIST = re.compile(r"\[(?P<items>[^\[\]]*)\]")


def _has_arch_type(items_block: str) -> bool:
    items = [t.strip().strip("'\"") for t in items_block.split(",")]
    return "arch" in items or "decision" in items


def _scan_commitlint_config(content: str) -> CheckStatus | None:
    """Return WARN if a type-enum without arch/decision is found, else None."""
    for m in _TYPE_ENUM_KEY.finditer(content):
        rest = content[m.end() :]
        list_match = _TYPES_LIST.search(rest)
        if not list_match:
            continue
        items_block = list_match.group("items")
        if not _has_arch_type(items_block):
            return CheckStatus.WARN
        return CheckStatus.OK
    return None


def check_commitlint_compat(repo_root: Path | None = None) -> CheckResult:
    root = repo_root or Path.cwd()
    candidates = [
        root / "commitlint.config.js",
        root / "commitlint.config.cjs",
        root / "commitlint.config.ts",
        root / "commitlint.config.mjs",
        root / ".commitlintrc",
        root / ".commitlintrc.json",
        root / ".commitlintrc.js",
        root / ".commitlintrc.yaml",
        root / ".commitlintrc.yml",
    ]
    pkg = root / "package.json"

    for path in candidates:
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        status = _scan_commitlint_config(content)
        if status is None:
            continue
        if status is CheckStatus.WARN:
            return CheckResult(
                name="toolchain.commitlint_compat",
                status=CheckStatus.WARN,
                detail=f"{path.name} type-enum missing arch/decision",
                suggestion=(
                    "Add 'arch' and 'decision' to type-enum:\n"
                    "  'type-enum': [2, 'always', "
                    "[..., 'arch', 'decision']]"
                ),
            )
        return CheckResult(
            name="toolchain.commitlint_compat",
            status=CheckStatus.OK,
            detail=f"{path.name} accepts arch/decision",
        )

    # package.json#commitlint as a fallback
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = None
        if data and "commitlint" in data:
            rules = data["commitlint"].get("rules", {})
            type_enum = rules.get("type-enum")
            if isinstance(type_enum, list) and len(type_enum) >= 3:
                allowed = type_enum[2]
                if isinstance(allowed, list) and not (
                    "arch" in allowed or "decision" in allowed
                ):
                    return CheckResult(
                        name="toolchain.commitlint_compat",
                        status=CheckStatus.WARN,
                        detail="package.json#commitlint missing arch/decision",
                        suggestion=(
                            "Add 'arch' and 'decision' to commitlint.rules.type-enum"
                        ),
                    )

    return CheckResult(
        name="toolchain.commitlint_compat",
        status=CheckStatus.OK,
        detail="no commitlint config found or accepts arch/decision",
    )
