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


@app.command("install-hooks")
def install_hooks_cmd(
    path: str = typer.Option(".", "--path", help="Repo path"),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Remove AXON-managed hooks instead of installing"
    ),
) -> None:
    """Install (or remove) AXON git hooks in a repo. Idempotent."""
    from axon.hooks.git_installer import install_hooks, uninstall_hooks

    if uninstall:
        removed = uninstall_hooks(path)
        typer.echo(f"removed: {', '.join(removed) or 'none'}")
    else:
        installed = install_hooks(path)
        typer.echo(f"installed: {', '.join(installed) or 'none'}")


if __name__ == "__main__":
    app()
