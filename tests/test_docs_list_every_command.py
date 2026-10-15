"""The documented CLI surface must match the CLI that ships.

`docs/PROJECT_OVERVIEW.md` is the only complete enumeration of the commands,
and it was two rounds behind: `seed-lessons` and `index-vault` from one round,
`rekey-repo` from the next, none of them listed. A reader looking for the way
to re-key mis-filed decisions would conclude there isn't one.

This is the same failure mode as the `pb` suggestions - documentation
describing a system that no longer exists - so it gets the same treatment: an
assertion against the shipped app rather than a promise to remember.
"""

from __future__ import annotations

from pathlib import Path

import typer.main

from axon.__main__ import app

OVERVIEW = Path(__file__).resolve().parents[1] / "docs" / "PROJECT_OVERVIEW.md"

def test_every_shipped_command_appears_in_the_overview() -> None:
    root = typer.main.get_command(app)
    shipped = set(root.commands)  # type: ignore[attr-defined]
    text = OVERVIEW.read_text()

    # Sub-apps are documented as `axon graph {index,neighbors,path}`, so accept
    # either form: the standalone name, or the group with its subcommand list.
    missing = sorted(
        c for c in shipped if f"`{c}`" not in text and f"`axon {c} {{" not in text
    )

    assert not missing, (
        f"{OVERVIEW.name} does not list: {missing}. It is the only complete "
        "enumeration of the CLI, so a command absent from it is undiscoverable."
    )
