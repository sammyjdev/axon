"""AXON CLI — agent-agnostic execution & context network.

Same context, any AI coding agent. This is the focused `axon` entry point
(T6.3). Legacy Prometheus-vault commands live in `axon.cli.pb` and are not
surfaced here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

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


@app.command()
def init(
    repo: str = typer.Argument(".", help="Repo path to initialize AXON in"),
) -> None:
    """Initialize AXON in a repo: install git hooks and index its code."""
    from axon.cli.pb import _get_db_path
    from axon.code.indexer import index_repo
    from axon.hooks.git_installer import install_hooks
    from axon.store.session_store import SessionStore

    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.exists():
        typer.echo(f"Repo not found: {repo_path}", err=True)
        raise typer.Exit(1)

    installed = install_hooks(repo_path)
    typer.echo(f"hooks installed: {', '.join(installed) or 'none'}")

    async def _index() -> int:
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            symbols = await index_repo(repo_path, store=store)
            return len(symbols)
        finally:
            await store.close()

    count = asyncio.run(_index())
    typer.echo(f"indexed {count} symbols from {repo_path}")


@app.command()
def serve() -> None:
    """Start the AXON MCP server (stdio transport)."""
    from axon.mcp.server import main as mcp_main

    mcp_main()


if __name__ == "__main__":
    app()
