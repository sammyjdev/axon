"""Integration with the ``pre-commit`` framework (dec-113).

Generates the YAML entry block users need to add to
``.pre-commit-config.yaml`` so AXON's hooks run alongside the rest of
their pipeline. Idempotent: re-running produces the same block.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def axon_repo_entry() -> str:
    """The ``- repo: local`` block users paste into pre-commit config."""
    py = sys.executable
    return (
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: axon-post-commit\n"
        "        name: AXON post-commit capture\n"
        "        entry: " + py + " -m axon.hooks.git_event commit\n"
        "        language: system\n"
        "        stages: [post-commit]\n"
        "        always_run: true\n"
        "        pass_filenames: false\n"
        "      - id: axon-post-merge\n"
        "        name: AXON post-merge revalidate drafts\n"
        "        entry: " + py + " -m axon.hooks.git_event post-merge\n"
        "        language: system\n"
        "        stages: [post-merge]\n"
        "        always_run: true\n"
        "        pass_filenames: false\n"
        "      - id: axon-post-checkout\n"
        "        name: AXON post-checkout revalidate drafts\n"
        "        entry: " + py + " -m axon.hooks.git_event post-checkout\n"
        "        language: system\n"
        "        stages: [post-checkout]\n"
        "        always_run: true\n"
        "        pass_filenames: false\n"
    )


def dry_run_message(config_path: Path) -> str:
    """Human-readable preview shown when ``--apply`` is not passed."""
    return (
        f"pre-commit framework detected at {config_path}\n"
        "AXON would add the following entry to its hooks list "
        "(use --apply to write):\n\n" + axon_repo_entry()
    )


def merge_into(config_path: Path) -> bool:
    """Append the AXON entry to the pre-commit config if not already present.

    Returns True if the file was modified, False if it already contained
    AXON entries. This is intentionally a simple append-if-missing — full
    YAML re-serialisation would require PyYAML, which AXON does not
    depend on by default.
    """
    if not config_path.exists():
        return False
    original = config_path.read_text(encoding="utf-8")
    # A config written by an older AXON still holds the pre-push entry; it goes
    # before the idempotency check, which would otherwise return first.
    text = _drop_retired_pre_push(original)
    if "id: axon-post-commit" not in text:
        if not text.endswith("\n"):
            text += "\n"
        text += axon_repo_entry()
    if text == original:
        return False
    config_path.write_text(text, encoding="utf-8")
    return True


_RETIRED_ID = re.compile(r"- id: axon-pre-push\s*$")


def _drop_retired_pre_push(text: str) -> str:
    """Remove the whole `- id: axon-pre-push` list item older versions wrote.

    The item is its id line plus every following line that is blank, a comment,
    or indented deeper than its dash. Stopping at a blank or comment line instead
    would leave its `entry` and `stages` under the hook above it, turning that
    hook into a push hook.
    """
    kept: list[str] = []
    item_depth: int | None = None
    for line in text.splitlines(keepends=True):
        body = line.lstrip(" ")
        depth = len(line) - len(body)
        if item_depth is not None:
            if not body.strip() or body.startswith("#") or depth > item_depth:
                continue
            item_depth = None
        if _RETIRED_ID.match(body):
            item_depth = depth
            continue
        kept.append(line)
    return "".join(kept)
