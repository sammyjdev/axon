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
    from axon.exceptions import GitAnchorError
    from axon.hooks.git_installer import install_hooks, uninstall_hooks

    try:
        if uninstall:
            removed = uninstall_hooks(path)
            typer.echo(f"removed: {', '.join(removed) or 'none'}")
        else:
            installed = install_hooks(path)
            typer.echo(f"installed: {', '.join(installed) or 'none'}")
    except GitAnchorError as exc:
        typer.echo(f"Not a git repository: {path} ({exc})", err=True)
        raise typer.Exit(1) from exc


@app.command()
def init(
    repo: str = typer.Argument(".", help="Repo path to initialize AXON in"),
) -> None:
    """Initialize AXON in a repo: install git hooks and index its code."""
    from axon.cli.pb import _get_db_path
    from axon.code.indexer import index_repo
    from axon.exceptions import GitAnchorError
    from axon.hooks.git_installer import install_hooks
    from axon.store.session_store import SessionStore

    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.exists():
        typer.echo(f"Repo not found: {repo_path}", err=True)
        raise typer.Exit(1)

    try:
        installed = install_hooks(repo_path)
    except GitAnchorError as exc:
        typer.echo(f"Not a git repository: {repo_path} ({exc})", err=True)
        raise typer.Exit(1) from exc
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


@app.command()
def health() -> None:
    """Report the health of each AXON subsystem (SQLite, Redis, Qdrant, mem0, vault, git)."""
    from axon.mcp.server import axon_health

    typer.echo(asyncio.run(axon_health()))


@app.command()
def status(
    repo: str = typer.Option(None, "--repo", help="Repo name (default: cwd basename)"),
) -> None:
    """Show the current repo, its decision count, and the latest decision."""
    from axon.cli.pb import _get_db_path
    from axon.store.session_store import SessionStore

    repo_name = repo or Path.cwd().name

    async def _decisions():
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            return await store.find_decisions_by_repo(repo_name, limit=20)
        finally:
            await store.close()

    decisions = asyncio.run(_decisions())
    typer.echo(f"repo: {repo_name}")
    typer.echo(f"decisions: {len(decisions)}")
    if decisions:
        latest = decisions[0]
        typer.echo(f"latest: {latest.summary} ({latest.id})")
    else:
        typer.echo("latest: none")


@app.command()
def export(
    doc_type: str = typer.Argument(..., help="adr | architecture | summary"),
    repo: str = typer.Option(None, "--repo", help="Repo name (default: cwd basename)"),
) -> None:
    """Export a repo's decisions to the Obsidian vault."""
    from datetime import date

    from axon.cli.pb import _get_db_path
    from axon.obsidian.discovery import discover_vault
    from axon.obsidian.exporter import (
        export_adr,
        export_architecture_doc,
        export_project_summary,
    )
    from axon.store.session_store import SessionStore

    vault = discover_vault()
    if vault is None:
        typer.echo("Obsidian vault not found (set AXON_VAULT).", err=True)
        raise typer.Exit(1)

    repo_name = repo or Path.cwd().name

    async def _decisions():
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            return await store.find_decisions_by_repo(repo_name, limit=100)
        finally:
            await store.close()

    decisions = asyncio.run(_decisions())
    if not decisions:
        typer.echo(f"No decisions for repo '{repo_name}'.")
        return

    if doc_type == "adr":
        paths = [export_adr(d, vault=vault) for d in decisions]
        typer.echo(f"exported {len(paths)} ADR notes to {vault}")
    elif doc_type == "architecture":
        path = export_architecture_doc(decisions, vault=vault, name=repo_name)
        typer.echo(f"exported architecture doc: {path}")
    elif doc_type == "summary":
        path = export_project_summary(repo_name, date.today(), decisions, vault=vault)
        typer.echo(f"exported summary: {path}")
    else:
        typer.echo(f"Unknown doc type: {doc_type} (adr|architecture|summary)", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Surviving AXON-relevant commands, re-registered from the legacy pb.py CLI.
# Cut commands (ask, index, watch, til, deep, expand, career, cost) are not
# imported and therefore not surfaced.
# ---------------------------------------------------------------------------
from axon.cli.pb import (  # noqa: E402
    adr_app,
    git_proxy,
    graph_app,
    profile_app,
    rtk,
    rtk_init,
    rtk_proxy,
    rtk_status,
    run_proxy,
    scan,
    search,
    session_app,
)

app.add_typer(adr_app, name="adr")
app.add_typer(graph_app, name="graph")
app.add_typer(profile_app, name="profile")
app.add_typer(session_app, name="session")

app.command("scan")(scan)
app.command("search")(search)
app.command("rtk")(rtk)
app.command("rtk-status")(rtk_status)
app.command("rtk-init")(rtk_init)
app.command("rtk-proxy")(rtk_proxy)
app.command("run")(run_proxy)
app.command("git")(git_proxy)

if __name__ == "__main__":
    app()
