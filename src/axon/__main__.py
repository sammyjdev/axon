"""AXON CLI — agent-agnostic execution & context network.

Same context, any AI coding agent. This is the single `axon` entry point
(T6.3, unified per dec-125). Commands are implemented in `axon.cli.pb` and
re-registered here; cut commands are deleted from `axon.cli.pb` itself, not
merely unsurfaced.
"""

from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import os
import subprocess
from collections import Counter
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

import typer


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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
        v = _pkg_version("axon-context-mcp")
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
    """Initialize AXON in a repo: install its git hooks."""
    from axon.exceptions import GitAnchorError
    from axon.hooks.git_installer import install_hooks

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


@app.command()
def serve() -> None:
    """Start the AXON MCP server (stdio transport)."""
    from axon.mcp.server import main as mcp_main

    mcp_main()


# Note: This bind guard cannot cover a direct `uvicorn axon.http.app:app` invocation,
# because the bind host only exists at the CLI layer.
@app.command("serve-http")
def serve_http(
    port: int = typer.Option(8765, "--port", "-p", help="TCP port to listen on."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev only)."),
    allow_unauthenticated: bool = typer.Option(
        False,
        "--allow-unauthenticated",
        help="Permit a non-loopback bind with no AXON_HTTP_TOKEN set.",
    ),
) -> None:
    """Start the AXON HTTP server: the dashboard and the activity API.

    Requires the 'http' optional extra::

        pip install axon-context-mcp[http]
    """
    token = os.environ.get("AXON_HTTP_TOKEN", "")
    env_allow = os.environ.get("AXON_HTTP_ALLOW_UNAUTHENTICATED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not _is_loopback(host) and not token and not allow_unauthenticated and not env_allow:
        typer.echo(
            f"Refusing to bind to non-loopback host '{host}' without authentication. "
            "Set AXON_HTTP_TOKEN, or pass --allow-unauthenticated "
            "or set AXON_HTTP_ALLOW_UNAUTHENTICATED=1.",
            err=True,
        )
        raise typer.Exit(1)
    if allow_unauthenticated:
        # The app reads the opt-in from the environment: with no token it serves
        # loopback hosts only, unless the operator said otherwise here.
        os.environ["AXON_HTTP_ALLOW_UNAUTHENTICATED"] = "1"

    try:
        import uvicorn
    except ModuleNotFoundError:
        typer.echo(
            "uvicorn is not installed. Run: pip install axon-context-mcp[http]",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Starting AXON HTTP server on http://{host}:{port}/dashboard")
    uvicorn.run(
        "axon.http.app:app",
        host=host,
        port=port,
        reload=reload,
    )


@app.command()
def health() -> None:
    """Report the health of each AXON subsystem (postgres, vault, git)."""
    from axon.mcp.server import axon_health

    typer.echo(asyncio.run(axon_health()))


@app.command()
def gain(
    json_out: bool = typer.Option(False, "--json", help="Emit raw GainSummary as JSON."),
) -> None:
    """Show compression-gain statistics: windows, saved tokens, and daily trend."""
    from axon.observability.gain import load_gain

    summary = load_gain()

    if summary.windows == 0:
        typer.echo("No compression telemetry yet. Run some compressions first.")
        return

    if json_out:
        typer.echo(summary.model_dump_json(indent=2))
        return

    # Build human-readable output
    lines: list[str] = [
        "AXON — context savings",
        f"  windows     {summary.windows:,}        ({summary.compressed} compressed)",
        (
            f"  saved       {summary.saved_tokens:,} tokens   "
            f"({summary.before_tokens:,} -> {summary.after_tokens:,})"
        ),
    ]

    # Ratio line: handle None percentiles
    if summary.p50_pct is None:
        ratio_line = "  ratio       n/a"
    else:
        ratio_parts = [f"p50 {summary.p50_pct}%"]
        if summary.mean_pct is not None:
            ratio_parts.append(f"mean {summary.mean_pct}%")
        if summary.p95_pct is not None:
            ratio_parts.append(f"p95 {summary.p95_pct}%")
        if summary.max_pct is not None:
            ratio_parts.append(f"max {summary.max_pct}%")
        ratio_str = " ".join(ratio_parts) if ratio_parts else "n/a"
        ratio_line = f"  ratio       {ratio_str}"
    lines.append(ratio_line)

    # Engines line
    engine_parts = []
    for engine, count in sorted(summary.by_engine.items()):
        engine_parts.append(f"{engine} {count}")
    lines.append(f"  engines     {', '.join(engine_parts)}")

    # Sparkline from daily_saved
    if summary.daily_saved:
        values = [v for _, v in summary.daily_saved]
        min_val = min(values)
        max_val = max(values)
        block_chars = "▁▂▃▄▅▆▇█"

        if min_val == max_val:
            # All values are the same
            sparkline = "".join([block_chars[-1]] * len(values))
        else:
            # Normalize to [0, 7] range for 8 block chars (indices 0-7)
            sparkline = "".join(
                block_chars[int((v - min_val) * 7 / (max_val - min_val))] for v in values
            )
        lines.append(f"  {sparkline}")

    typer.echo("\n".join(lines))


@app.command()
def status(
    repo: str = typer.Option(None, "--repo", help="Repo name (default: cwd basename)"),
) -> None:
    """Show the current repo, its decision count, and the latest decision."""
    from axon.cli.pb import _get_db_path
    from axon.core.repo_identity import repo_identity
    from axon.store.session_store import SessionStore

    repo_name = repo or repo_identity()

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
    from axon.core.repo_identity import repo_identity
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

    repo_name = repo or repo_identity()

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


@app.command("rekey-repo")
def rekey_repo(
    checkout: Path = typer.Argument(..., help="Checkout that owns the commits"),
    apply: bool = typer.Option(False, "--apply", help="Write changes (default: dry run)"),
    only_key: list[str] = typer.Option(
        [], "--only-key", help="Move only source keys matching this glob (repeatable)"
    ),
    all: bool = typer.Option(False, "--all", help="Move every provably reachable source key"),
) -> None:
    """Re-key decisions mis-filed under a worktree directory name (dec-129).

    Applying requires --only-key or --all. A row moves only when its git_hash
    is provably reachable in CHECKOUT, so nothing is guessed. Rows are updated
    in place, never deleted.
    """
    from axon.cli.pb import _get_db_path
    from axon.core.decision import Decision
    from axon.core.repo_identity import repo_identity
    from axon.store.session_store import SessionStore

    if apply and not only_key and not all:
        typer.echo("Refusing to move rows: pass --only-key or --all with --apply.", err=True)
        raise typer.Exit(2)
    if all and only_key:
        # They read as "everything" and "only these", and the selection below
        # let --all win: an operator narrowing an already-destructive run with
        # --only-key would silently get the widest possible scope instead.
        typer.echo(
            "--all and --only-key are mutually exclusive: --all moves every "
            "reachable key, --only-key moves just the ones you name.",
            err=True,
        )
        raise typer.Exit(2)

    target = repo_identity(checkout)
    moved: list[tuple[str, str]] = []

    def _reachable(git_hash: str) -> bool:
        return (
            subprocess.run(  # noqa: S603, S607
                [  # noqa: S607
                    "git",
                    "-C",
                    str(checkout),
                    "cat-file",
                    "-e",
                    f"{git_hash}^{{commit}}",
                ],
                capture_output=True,
            ).returncode
            == 0
        )

    async def _run() -> None:
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            # ponytail: one cat-file per row (~350 rows, one-off).
            # --batch-check on stdin if it ever matters.
            for decision in await store.all_decisions():
                if (
                    decision.repo == target
                    or (
                        not all
                        and only_key
                        and not any(fnmatch.fnmatch(decision.repo, pattern) for pattern in only_key)
                    )
                    or not decision.git_hash
                    or not _reachable(decision.git_hash)
                ):
                    continue
                moved.append((decision.id, decision.repo))
                if apply:
                    await store.save_decision(
                        Decision.model_validate(
                            {**decision.model_dump(mode="python"), "repo": target}
                        )
                    )
        finally:
            await store.close()

    asyncio.run(_run())
    for decision_id, source in moved:
        typer.echo(f"{decision_id}: {source} -> {target}")
    for source, count in sorted(Counter(source for _, source in moved).items()):
        typer.echo(f"  {source}: {count} row(s)")
    if apply:
        typer.echo(f"re-keyed {len(moved)} decision(s) to '{target}'")
    else:
        typer.echo(
            f"would re-key {len(moved)} decision(s) to '{target}' "
            "(dry run; moving requires --apply with --only-key or --all)"
        )


# ---------------------------------------------------------------------------
# Surviving AXON-relevant commands, re-registered from the legacy pb.py CLI.
# The code-index commands (search, sync, index-*, graph, scan) were deleted with
# code vectorization (dec-138).
# ---------------------------------------------------------------------------
from axon.cli.pb import (  # noqa: E402
    activity_app,
    adr_app,
    compact_hook,
    configure,
    doctor,
    hooks_app,
    note,
    pending_app,
    profile_app,
    seed_lessons,
    session_app,
    session_hook,
    session_save,
    setup,
    why,
)
from axon.cli.pb import init as pb_bootstrap  # noqa: E402

app.add_typer(activity_app, name="activity")
app.add_typer(adr_app, name="adr")
app.add_typer(hooks_app, name="hooks")
app.add_typer(pending_app, name="pending")
app.add_typer(profile_app, name="profile")
app.add_typer(session_app, name="session")

app.command("doctor")(doctor)
app.command("bootstrap")(pb_bootstrap)
app.command("setup")(setup)
app.command("configure")(configure)
app.command("note")(note)
app.command("session-save")(session_save)
app.command("seed-lessons")(seed_lessons)
app.command("session-hook")(session_hook)
app.command("why")(why)
app.command("compact-hook")(compact_hook)

if __name__ == "__main__":
    app()
