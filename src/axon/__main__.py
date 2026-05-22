"""AXON CLI — agent-agnostic execution & context network.

Same context, any AI coding agent. This is the focused `axon` entry point
(T6.3). Legacy Prometheus-vault commands live in `axon.cli.pb` and are not
surfaced here.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="axon",
    help="AXON — same context, any AI coding agent.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def main() -> None:
    """AXON — same context, any AI coding agent."""
    pass


if __name__ == "__main__":
    app()
