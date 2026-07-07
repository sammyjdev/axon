"""AXON CLI — agent-agnostic execution & context network.

Same context, any AI coding agent. This is the focused `axon` entry point
(T6.3). Legacy AXON-vault commands live in `axon.cli.pb` and are not
surfaced here.
"""

from __future__ import annotations

import asyncio
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
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
        import uuid

        from axon.observability.trace_store import TraceStore

        store = SessionStore(_get_db_path())
        await store.init()

        # Emit index-start activity (best-effort; never breaks indexing)
        _recorder = None
        try:
            _trace_store = TraceStore()
            _recorder = _trace_store.recorder(
                trace_id=uuid.uuid4().hex,
                caller="cli",
            )
            _recorder.append_stage(
                "index",
                payload={"phase": "start", "target": str(repo_path)},
            )
        except Exception:
            pass

        try:
            symbols = await index_repo(repo_path, store=store)
            count_sym = len(symbols)
        finally:
            await store.close()

        # Emit index-done stage (best-effort)
        try:
            if _recorder is not None:
                _recorder.append_stage(
                    "index",
                    payload={"phase": "done", "symbols": count_sym},
                )
        except Exception:
            pass

        return count_sym

    count = asyncio.run(_index())
    typer.echo(f"indexed {count} symbols from {repo_path}")


@app.command()
def familiar(
    frames: int = typer.Option(
        0,
        "--frames",
        help="Exit after N render ticks (0 = run live until Ctrl+C). Useful for CI.",
    ),
) -> None:
    """Launch the AXON familiar — a live terminal companion driven by TraceStore activity."""
    from axon.pet.familiar import main as _familiar_main

    _frames: int | None = frames if frames > 0 else None
    asyncio.run(_familiar_main(frames=_frames))


@app.command()
def serve() -> None:
    """Start the AXON MCP server (stdio transport)."""
    from axon.mcp.server import main as mcp_main

    mcp_main()


@app.command("serve-http")
def serve_http(
    port: int = typer.Option(8765, "--port", "-p", help="TCP port to listen on."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev only)."),
) -> None:
    """Start the AXON OpenAI-compatible HTTP server.

    Exposes POST /v1/chat/completions so external evaluators (e.g. gnomon-eval)
    can measure recall quality.  The MCP stdio path is unchanged.

    Requires the 'http' optional extra::

        pip install axon-mcp[http]

    Point gnomon at it with base_url = http://localhost:8765/v1
    """
    try:
        import uvicorn
    except ModuleNotFoundError:
        typer.echo(
            "uvicorn is not installed. Run: pip install axon-mcp[http]",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Starting AXON HTTP server on http://{host}:{port}/v1")
    uvicorn.run(
        "axon.http.app:app",
        host=host,
        port=port,
        reload=reload,
    )


@app.command()
def health() -> None:
    """Report the health of each AXON subsystem (SQLite, Redis, pgvector, vault, git)."""
    from axon.mcp.server import axon_health

    typer.echo(asyncio.run(axon_health()))


@app.command()
def gain(
    json_out: bool = typer.Option(
        False, "--json", help="Emit raw GainSummary as JSON."
    ),
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
                block_chars[int((v - min_val) * 7 / (max_val - min_val))]
                for v in values
            )
        lines.append(f"  {sparkline}")

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


@app.command("ingest-vault")
def ingest_vault_cmd(
    vault: str | None = typer.Option(
        None, "--vault", help="Path to the Obsidian vault (overrides AXON_VAULT / auto-discovery)."
    ),
    provider: str = typer.Option(
        "litellm", "--provider", help="LLM provider: 'litellm' or 'anthropic'."
    ),
    model: str = typer.Option(
        "ollama/llama3", "--model", help="Model name passed to the provider."
    ),
    base_url: str | None = typer.Option(
        "http://localhost:11434",
        "--base-url",
        help="Base URL for the LLM endpoint (Ollama default).",
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", help="API key for the LLM provider (omit for local Ollama)."
    ),
    ctx: str = typer.Option(
        "personal", "--ctx", help="Context/collection for the note vectors (default: personal)."
    ),
    no_vectors: bool = typer.Option(
        False, "--no-vectors", help="Skip vector indexing; write only the SQLite graph."
    ),
) -> None:
    """Ingest an Obsidian vault into the AXON knowledge graph.

    Walks every ``.md`` file in the vault, extracts entities and relations
    with GLYPH's notes schema (via the configured LLM), and writes them into
    BOTH the SQLite graph (entities/relations) AND the vector ``--ctx``
    collection (note text) so ``ask`` / ``search_code`` / the HTTP endpoint
    retrieve the notes via the primary vector path.

    Defaults to a local Ollama endpoint — no API key required.
    """
    from axon.cli.pb import _get_db_path
    from axon.obsidian.importer import ingest_vault
    from axon.store.session_store import SessionStore

    vault_path = Path(vault).expanduser().resolve() if vault else None

    async def _run() -> tuple[Path | None, int, int, int]:
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            n_nodes, n_edges, n_vectors = await ingest_vault(
                store,
                vault_path=vault_path,
                provider=provider,
                model=model,
                base_url=base_url or None,
                api_key=api_key,
                index_vectors=not no_vectors,
                ctx=ctx,
            )
            # discover_vault may have resolved the path; retrieve it for the summary
            from axon.obsidian.discovery import discover_vault as _dv

            resolved = vault_path or _dv()
            return resolved, n_nodes, n_edges, n_vectors
        finally:
            await store.close()

    try:
        resolved_vault, n_nodes, n_edges, n_vectors = asyncio.run(_run())
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"vault:    {resolved_vault}")
    typer.echo(f"nodes:    {n_nodes}")
    typer.echo(f"edges:    {n_edges}")
    typer.echo(f"vectors:  {n_vectors} (ctx={ctx})")


# ---------------------------------------------------------------------------
# Surviving AXON-relevant commands, re-registered from the legacy pb.py CLI.
# Cut commands (ask, index, watch, til, deep, expand, career, cost) are not
# imported and therefore not surfaced.
# ---------------------------------------------------------------------------
from axon.cli.pb import (  # noqa: E402
    adr_app,
    doctor,
    git_proxy,
    graph_app,
    profile_app,
    rtk,
    rtk_init,
    rtk_install_cmd,
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
app.command("rtk-install")(rtk_install_cmd)
app.command("rtk-proxy")(rtk_proxy)
app.command("run")(run_proxy)
app.command("git")(git_proxy)
app.command("doctor")(doctor)

if __name__ == "__main__":
    app()
