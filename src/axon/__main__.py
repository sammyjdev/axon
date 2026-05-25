"""AXON CLI — agent-agnostic execution & context network.

Same context, any AI coding agent. This is the focused `axon` entry point
(T6.3). Legacy AXON-vault commands live in `axon.cli.pb` and are not
surfaced here.
"""

from __future__ import annotations

import asyncio
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

import typer

app = typer.Typer(
    name="axon",
    help="AXON — same context, any AI coding agent.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if not value:
        return
    try:
        v = _pkg_version("axon-mcp")
    except PackageNotFoundError:
        v = "unknown"
    typer.echo(f"axon {v}")
    raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
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
def doctor(
    stale_days: int = typer.Option(
        7, "--stale-days", help="Threshold (days) after which an activity is reported as stale."
    ),
) -> None:
    """Validate the AXON + RTK + caveman stack: presence (binaries) and liveness (recent activity)."""
    import shutil
    import subprocess
    import sys
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=stale_days)

    def fmt_age(ts: datetime) -> str:
        delta = now - ts
        if delta.days >= 1:
            return f"{delta.days}d ago"
        hours = delta.seconds // 3600
        return f"{hours}h ago" if hours else "just now"

    lines: list[str] = ["# AXON doctor", "", "## Presence"]

    # axon CLI: trivially present (we're running)
    try:
        v = _pkg_version("axon-mcp")
    except PackageNotFoundError:
        v = "unknown"
    lines.append(f"- axon: ok ({v})")

    # rtk binary
    rtk_path = shutil.which("rtk")
    if rtk_path:
        try:
            rtk_v = subprocess.check_output(
                [rtk_path, "--version"], text=True, timeout=3
            ).strip()
        except Exception:
            rtk_v = "unknown"
        lines.append(f"- rtk: ok ({rtk_v})")
    else:
        lines.append("- rtk: not installed")

    # caveman engine: an internal axon module, not a CLI
    try:
        from axon.router.compressor import caveman_compress  # noqa: F401

        lines.append("- caveman engine: ok (axon.router.compressor)")
    except Exception as exc:
        lines.append(f"- caveman engine: error ({exc})")

    lines += ["", "## Liveness"]

    # AXON decisions: most recent across any repo
    from axon.cli.pb import _get_db_path
    from axon.store.session_store import SessionStore

    async def _latest_decision_ts() -> datetime | None:
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            import aiosqlite

            async with store._lock:  # noqa: SLF001
                db = await store._connection()  # noqa: SLF001
                db.row_factory = aiosqlite.Row
                rows = await db.execute_fetchall(
                    "SELECT created_at FROM decisions ORDER BY created_at DESC LIMIT 1"
                )
            if not rows:
                return None
            return datetime.fromisoformat(rows[0]["created_at"])
        finally:
            await store.close()

    try:
        latest_dec_ts = asyncio.run(_latest_decision_ts())
        if latest_dec_ts is None:
            lines.append("- axon captures: none yet (commit something in an axon-init'd repo)")
        else:
            if latest_dec_ts.tzinfo is None:
                latest_dec_ts = latest_dec_ts.replace(tzinfo=timezone.utc)
            tag = "stale" if latest_dec_ts < stale_cutoff else "ok"
            lines.append(f"- axon captures: {tag} (last {fmt_age(latest_dec_ts)})")
    except Exception as exc:
        lines.append(f"- axon captures: error ({exc})")

    # Compression telemetry: any record + presence of caveman engine recently
    try:
        from axon.observability.compression_telemetry import CompressionTelemetryStore

        store = CompressionTelemetryStore()
        records = store.load_all()
        if not records:
            lines.append("- compression telemetry: none yet")
        else:
            latest = records[-1]
            latest_ts = datetime.fromisoformat(latest.ts)
            if latest_ts.tzinfo is None:
                latest_ts = latest_ts.replace(tzinfo=timezone.utc)
            tag = "stale" if latest_ts < stale_cutoff else "ok"
            lines.append(
                f"- compression telemetry: {tag} ({len(records)} records, last {fmt_age(latest_ts)})"
            )
            caveman_recent = [
                r for r in records[-50:] if r.engine.startswith("caveman/")
            ]
            if caveman_recent:
                lines.append(
                    f"- caveman engine activity: ok ({len(caveman_recent)} of last 50 records)"
                )
            else:
                lines.append(
                    "- caveman engine activity: not seen in last 50 records (compression may be falling back)"
                )
    except Exception as exc:
        lines.append(f"- compression telemetry: error ({exc})")

    # RTK activity: mtime of history.db
    if sys.platform == "darwin":
        rtk_db = Path.home() / "Library" / "Application Support" / "rtk" / "history.db"
    else:
        rtk_db = Path.home() / ".local" / "share" / "rtk" / "history.db"
    if rtk_db.exists():
        rtk_ts = datetime.fromtimestamp(rtk_db.stat().st_mtime, tz=timezone.utc)
        tag = "stale" if rtk_ts < stale_cutoff else "ok"
        lines.append(f"- rtk activity: {tag} (history.db touched {fmt_age(rtk_ts)})")
    else:
        lines.append(f"- rtk activity: not found ({rtk_db})")

    typer.echo("\n".join(lines))


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
