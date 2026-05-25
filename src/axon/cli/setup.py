from __future__ import annotations

import subprocess as _sp
from pathlib import Path

import typer

from axon.cli.setup_session import SetupSession

_TRANSPORT_OPTIONS = ("1", "2", "3")


def run_step_transport(session: SetupSession) -> SetupSession:
    typer.echo("\n── Step 1/3: Deployment ──────────────────────────────")
    typer.echo("Where will AXON run?\n")
    typer.echo("  [1] Local (Claude Code / Copilot / Cursor)  → stdio")
    typer.echo("  [2] Local with Claude Web/App               → HTTP + tunnel")
    typer.echo("  [3] Server / VPS                            → HTTP direct\n")

    while True:
        choice = typer.prompt("Choice [1/2/3]").strip()
        if choice in _TRANSPORT_OPTIONS:
            break
        typer.echo("Please enter 1, 2, or 3.", err=True)

    if choice == "1":
        return SetupSession(**{**session.__dict__, "transport": "stdio"})

    if choice == "2":
        while True:
            port_str = typer.prompt("Port", default="8080")
            try:
                port = int(port_str)
                break
            except ValueError:
                typer.echo("Port must be a number (e.g. 8080).", err=True)
        return SetupSession(**{**session.__dict__, "transport": "http", "http_port": port})

    host = typer.prompt("Host", default="0.0.0.0")
    while True:
        port_str = typer.prompt("Port", default="8080")
        try:
            port = int(port_str)
            break
        except ValueError:
            typer.echo("Port must be a number (e.g. 8080).", err=True)
    return SetupSession(
        **{**session.__dict__, "transport": "http", "http_host": host, "http_port": port}
    )


_LANGUAGE_MAP: dict[str, str] = {
    "1": "python",
    "2": "kotlin",
    "3": "typescript",
    "4": "go",
    "5": "rust",
}
_PROFILE_MAP: dict[str, str] = {
    "1": "solo-dev",
    "2": "team-dev",
    "3": "privacy-first",
}


def run_step_domain(session: SetupSession) -> SetupSession:
    typer.echo("\n── Step 2/3: Domain ──────────────────────────────────")
    typer.echo("Which languages do you mainly work with? (multiple choice)\n")
    typer.echo("  [1] Python   [2] Kotlin/Java   [3] TypeScript/JS")
    typer.echo("  [4] Go       [5] Rust           [6] Other\n")

    raw = typer.prompt("Choice(s), comma-separated (e.g. 1,3)").strip()
    choices = {c.strip() for c in raw.split(",")}
    languages = tuple(
        sorted({_LANGUAGE_MAP[c] for c in choices if c in _LANGUAGE_MAP})
    )

    typer.echo("\nUsage profile?\n")
    typer.echo("  [1] Solo development    → solo-dev")
    typer.echo("  [2] Team / company      → team-dev")
    typer.echo("  [3] Strict privacy      → privacy-first\n")

    while True:
        profile_choice = typer.prompt("Choice [1/2/3]").strip()
        if profile_choice in _PROFILE_MAP:
            break
        typer.echo("Please enter 1, 2, or 3.", err=True)

    return SetupSession(
        **{**session.__dict__, "languages": languages, "profile": _PROFILE_MAP[profile_choice]}
    )


_VAULT_CONTEXT_MAP: dict[str, str] = {
    "1": "personal",
    "2": "knowledge",
    "3": "career",
    "4": "saas",
}


def run_step_vault(session: SetupSession) -> SetupSession:
    typer.echo("\n── Step 3/3: Vault ───────────────────────────────────")
    typer.echo("Which contexts do you want in your vault? (multiple choice)\n")
    typer.echo("  [1] personal    ← personal projects")
    typer.echo("  [2] knowledge   ← notes, docs, references")
    typer.echo("  [3] career      ← CV, professional history")
    typer.echo("  [4] saas        ← SaaS projects\n")

    raw = typer.prompt("Choice(s), comma-separated (e.g. 1,2)").strip()
    choices = {c.strip() for c in raw.split(",")}
    contexts = tuple(
        sorted({_VAULT_CONTEXT_MAP[c] for c in choices if c in _VAULT_CONTEXT_MAP})
    )

    typer.echo("\nDo you want a private/restricted context (work)?\n")
    typer.echo("  [1] Yes — creates vault/work/ with mandatory explicit access")
    typer.echo("  [2] No\n")

    while True:
        work_choice = typer.prompt("Choice [1/2]").strip()
        if work_choice in ("1", "2"):
            break
        typer.echo("Please enter 1 or 2.", err=True)

    return SetupSession(
        **{
            **session.__dict__,
            "vault_contexts": contexts,
            "include_work_context": work_choice == "1",
        }
    )


_VAULT_READMES: dict[str, str] = {
    "personal": "Personal projects and experiments.",
    "knowledge": "Notes, documentation, and references.",
    "career": "CV, professional history, and interview prep.",
    "saas": "SaaS products and startup projects.",
    "work": (
        "Restricted work context.\n\n"
        "Access requires explicit `ctx='work'` — never included in default searches."
    ),
}


def run_step_commit(
    session: SetupSession,
    *,
    config_path: Path,
    vault_root: Path,
    packs_root: Path | None = None,
) -> list[str]:
    messages: list[str] = []

    # 1. Write [mcp] section to axon.toml
    _write_mcp_section(session, config_path)
    messages.append(f"Config updated: {config_path}")

    # 2. Scaffold vault directories
    all_contexts = list(session.vault_contexts)
    if session.include_work_context:
        all_contexts.append("work")
    for ctx in all_contexts:
        ctx_dir = vault_root / ctx
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / ".gitkeep").touch()
        readme = ctx_dir / "README.md"
        if not readme.exists():
            readme.write_text(
                f"# {ctx}\n\n{_VAULT_READMES.get(ctx, ctx)}\n",
                encoding="utf-8",
            )
    messages.append(
        f"Vault scaffolded: {vault_root} ({', '.join(all_contexts) or 'no contexts'})"
    )

    # 3. Validate domain packs
    if session.languages and packs_root is not None:
        from axon.domains.pack import load_domain_pack

        loaded: list[str] = []
        for lang in session.languages:
            pack_path = packs_root / f"{lang}.json"
            if pack_path.exists():
                load_domain_pack(pack_path)  # raises if invalid
                loaded.append(lang)
        if loaded:
            messages.append(f"Domain packs validated: {', '.join(loaded)}")

    # 4. Smoke test: compile src
    src_root = Path(__file__).parents[3] / "src" / "axon"
    result = _sp.run(
        ["python3", "-m", "compileall", "-q", str(src_root)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        messages.append("Smoke test: OK (compileall passed)")
    else:
        messages.append(
            f"Smoke test: WARNING — compileall reported issues\n{result.stderr.strip()}"
        )

    return messages


def format_next_steps(session: SetupSession) -> str:
    if session.transport == "stdio":
        return (
            'Add AXON to your MCP config:\n\n'
            '  {\n'
            '    "mcpServers": {\n'
            '      "axon": {\n'
            '        "command": "pb",\n'
            '        "args": ["mcp", "serve"]\n'
            '      }\n'
            '    }\n'
            '  }\n\n'
            'Then run: pb index'
        )

    port = session.http_port or 8080
    if session.http_host is None:
        return (
            f"Start the server and expose it:\n\n"
            f"  pb mcp serve --transport http --port {port}\n"
            f"  # in another terminal:\n"
            f"  ngrok http {port}          # or: cloudflare tunnel\n\n"
            f"Add the public URL to Claude Web → Settings → Integrations → MCP.\n"
            f"Then run: pb index"
        )

    return (
        f"Start the server:\n\n"
        f"  pb mcp serve --transport http --host 0.0.0.0 --port {port}\n\n"
        f"Configure your reverse proxy to forward to port {port}.\n"
        f"Add the public URL to Claude Web → Settings → Integrations → MCP.\n"
        f"Then run: pb index"
    )


def run_setup(
    *,
    config_path: Path,
    vault_root: Path,
    packs_root: Path,
) -> None:
    typer.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    typer.echo("  AXON Setup Wizard")
    typer.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    session = SetupSession()
    session = run_step_transport(session)
    session = run_step_domain(session)
    session = run_step_vault(session)

    typer.echo("\n── Applying configuration ────────────────────────────")
    messages = run_step_commit(
        session,
        config_path=config_path,
        vault_root=vault_root,
        packs_root=packs_root,
    )
    for msg in messages:
        typer.echo(f"  ✓ {msg}")

    typer.echo("\n── Next steps ────────────────────────────────────────")
    typer.echo(format_next_steps(session))
    typer.echo("\nSetup complete.")


def _write_mcp_section(session: SetupSession, config_path: Path) -> None:
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    if "[mcp]" in content:
        return  # already configured, do not overwrite

    lines = ["\n[mcp]", f'transport = "{session.transport}"']
    if session.transport == "http":
        if session.http_port is not None:
            lines.append(f"port = {session.http_port}")
        if session.http_host is not None:
            lines.append(f'host = "{session.http_host}"')

    config_path.write_text(content.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
