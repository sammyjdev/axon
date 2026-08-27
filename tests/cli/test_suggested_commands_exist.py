"""Every command this codebase tells an operator to run must actually exist.

`axon doctor` suggested `pb adr validate-drafts` and `pb pending drain`, and
`axon setup` printed `Then run: pb index` — `pb` was retired in dec-125 and
`index` was deleted outright. An operator following a diagnostic's own advice
got "command not found" and concluded the install was broken.

A grep for the string `pb ` would not have caught it for long: dec-125 already
retired that binary once and the references came back. This asserts the
stronger property — the suggested command resolves against the CLI that ships.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer.main

from axon.__main__ import app

SRC = Path(__file__).resolve().parents[2] / "src" / "axon"

#: Only forms that are unambiguously a command being suggested: inside a
#: backtick span, or after "run:" / "Run ". Bare "axon <word>" is prose - the
#: project is called axon, so "axon captures decisions" is a sentence, not a
#: command, and matching it produced eight false failures.
_SUGGESTION = re.compile(
    r"(?:`axon |[Rr]un: axon |[Rr]un axon )([a-z][a-z0-9-]*)(?: ([a-z][a-z0-9-]*))?"
)


def _shipped_commands() -> dict[str, set[str]]:
    """Command names on the installed entry point, and their subcommands."""
    root = typer.main.get_command(app)
    out: dict[str, set[str]] = {}
    for name, cmd in root.commands.items():  # type: ignore[attr-defined]
        out[name] = set(getattr(cmd, "commands", {}))
    return out


def _suggestions() -> list[tuple[Path, int, str, str]]:
    found = []
    for path in SRC.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if '"' not in line and "'" not in line:
                continue
            for group, sub in _SUGGESTION.findall(line):
                found.append((path, lineno, group, sub))
    return found


def test_the_codebase_suggests_at_least_one_command() -> None:
    """Guards the guard: a regex that matches nothing would pass silently."""
    assert _suggestions(), "no `axon ...` suggestions found — regex is broken"


@pytest.mark.parametrize("path,lineno,group,sub", _suggestions())
def test_every_suggested_command_exists(
    path: Path, lineno: int, group: str, sub: str
) -> None:
    shipped = _shipped_commands()
    where = f"{path.name}:{lineno}"

    assert group in shipped, f"{where} suggests `axon {group}`, which does not exist"

    # A second word is only a subcommand when the first is a command group;
    # otherwise it is an argument or prose, and there is nothing to verify.
    if sub and shipped[group]:
        assert sub in shipped[group], (
            f"{where} suggests `axon {group} {sub}`, but {group} has no such subcommand"
        )


def test_no_retired_binary_in_text_shown_to_an_operator() -> None:
    """Scoped to output, not to module docstrings.

    A docstring saying ``pb adr audit`` reads back this log is developer
    history and harmless. A `suggestion=` field is printed by `axon doctor`
    as the remedy for a failing check, and `cli/setup.py` prints the
    post-install instructions - those are read by a person who will type them.
    """
    offenders = []
    targets = [SRC / "cli" / "setup.py", *(SRC / "doctor").rglob("*.py")]
    for path in targets:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"""["'`]\s*pb [a-z]""", line) and not line.strip().startswith("#"):
                offenders.append(f"{path.name}:{lineno}: {line.strip()[:70]}")
    assert not offenders, "retired `pb` binary in operator-facing text:\n" + "\n".join(
        offenders
    )
