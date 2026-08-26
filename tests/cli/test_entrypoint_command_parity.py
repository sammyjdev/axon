"""Guard: every command on pb's Typer app must be reachable from the installed
`axon` entry point (`axon.__main__:app`).

Issue #142 shipped `seed-lessons` registered only on `axon.cli.pb`'s app while
the installed binary is a SEPARATE Typer app that re-registers pb's commands
explicitly. Every test drove `pb.app`, so the gate stayed green while
`axon seed-lessons` answered `No such command`. This guard compares CALLBACKS
(the undecorated function objects inside typer's CommandInfo), not command
names, so a rename (pb's `init` is exposed as `bootstrap`) does not
false-positive while a genuinely unregistered function still does.

The parity assertion is strict - there is NO allowlist. Issue #147 closed
the last known gap (`index-vault` was registered only on pb's app): any
function reachable on `pb.app` but not on the entry-point app fails here
immediately instead of hiding behind an exclusion list that can go stale.

Never assert on Rich-rendered help text here (see
tests/cli/test_seed_lessons_cli.py): help output depends on terminal width,
TTY detection and resolved Typer/Rich versions. Click's command registry is
the stable surface.
"""

from __future__ import annotations

import typer.main

from axon.__main__ import app as main_app
from axon.cli import pb


def _callbacks(app: typer.Typer) -> set[object]:
    return {ci.callback for ci in app.registered_commands}


def test_every_pb_command_is_reachable_from_the_entrypoint() -> None:
    missing = _callbacks(pb.app) - _callbacks(main_app)
    assert missing == set(), f"unreachable from `axon`: {missing}"
    # Not vacuous: pb registers real commands.
    assert _callbacks(pb.app)


def test_every_pb_group_is_reachable_from_the_entrypoint() -> None:
    pb_groups = {info.typer_instance for info in pb.app.registered_groups}
    entrypoint_groups = {
        info.typer_instance for info in main_app.registered_groups
    }

    assert pb_groups, "pb.app must register at least one sub-app group"
    assert pb_groups <= entrypoint_groups


def test_entrypoint_exposes_doctor() -> None:
    """Pin the surface issue #141 extends: `axon doctor` must exist."""
    commands = typer.main.get_command(main_app).commands  # type: ignore[attr-defined]
    assert "doctor" in commands
