"""Husky integration helpers (dec-113).

We refuse to mutate ``.husky/`` files automatically — husky's contract
with package.json + lifecycle scripts makes silent edits risky. Instead
we emit a wrapper text the user pastes manually into their husky hook.
"""

from __future__ import annotations

import sys


def wrapper_text(event: str) -> str:
    """The shell snippet to paste into ``.husky/{hook}``.

    ``event`` is ``post-commit``, ``pre-push``, ``post-merge`` or
    ``post-checkout``. Failure is swallowed so husky chains continue.
    """
    py = sys.executable
    logical = {
        "post-commit": "commit",
        "pre-push": "push",
        "post-merge": "post-merge",
        "post-checkout": "post-checkout",
    }.get(event, event)
    return (
        "# >>> AXON >>>\n"
        f"{py} -m axon.hooks.git_event {logical} 2>/dev/null || true\n"
        "# <<< AXON <<<\n"
    )


def dry_run_message() -> str:
    """Multi-hook preview message shown when ``--apply`` is not passed."""
    hooks = ["post-commit", "pre-push", "post-merge", "post-checkout"]
    lines = [
        "husky detected. AXON will NOT modify your .husky/ files.",
        "Paste the following lines into each husky hook:",
        "",
    ]
    for h in hooks:
        lines.append(f"# In .husky/{h}:")
        lines.append(wrapper_text(h).rstrip())
        lines.append("")
    return "\n".join(lines)
