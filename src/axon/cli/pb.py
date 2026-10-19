from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from axon.store.session_store import SessionMemory

import typer

from axon.config.runtime import load_runtime_config
from axon.context.registry import VALID_CONTEXTS

app = typer.Typer(
    name="axon",
    help="AXON CLI — segundo cérebro do Sammy",
    no_args_is_help=True,
)
adr_app = typer.Typer(help="Gerencia ADRs (Architectural Decision Records)")
session_app = typer.Typer(help="Gerencia sessão de contexto ativa")
profile_app = typer.Typer(help="Perfis de instalação e uso")
pending_app = typer.Typer(help="Gerencia o backlog .axon/pending/ (dec-112)")
hooks_app = typer.Typer(help="Instala hooks AXON (dec-113, opt-in com --apply)")
activity_app = typer.Typer(help="Historico operacional (Claude Code/Codex/AGY) - dec-136")

app.add_typer(adr_app, name="adr")
app.add_typer(session_app, name="session")
app.add_typer(profile_app, name="profile")
app.add_typer(pending_app, name="pending")
app.add_typer(hooks_app, name="hooks")
app.add_typer(activity_app, name="activity")

_DEFAULT_LESSON_CORPUS_PATH = (
    Path.home() / ".claude" / "agents" / "forge" / "lessons" / "agent-errors.json"
)
_RUNTIME = load_runtime_config()
_CTX_HELP = f"Contexto: {'|'.join(VALID_CONTEXTS)}"
_RUNTIME_MODES = ("full-local", "hybrid-local", "remote-infra", "minimal")
_CONFIGURE_USE_CASES = ("solo", "team", "corporate")
_CONFIGURE_PRIVACY_LEVELS = ("public", "internal", "confidential", "restricted")
_CONFIGURE_HARDWARE_OPTIONS = ("cpu-only", "mac-laptop", "nvidia", "linux-workstation")
_CONFIGURE_CLOUD_POLICIES = ("ok", "avoid", "deny")
_CONFIGURE_INFRA_OPTIONS = ("local", "remote")
_CONFIGURE_MEMORY_OPTIONS = ("light", "full")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_db_path() -> Path:
    return _RUNTIME.db_path


def _resolve_ctx(ctx: str | None, require_work_confirmation: bool = True) -> str | None:
    if ctx == "work" and require_work_confirmation:
        confirmed = typer.confirm("Acesso ao contexto work requer confirmação. Continuar?")
        if not confirmed:
            raise typer.Abort()
    return ctx


def _normalize_configure_value(
    field: str,
    value: str | None,
    *,
    allowed: tuple[str, ...],
) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in allowed:
        allowed_values = "|".join(allowed)
        raise typer.BadParameter(f"{field} must be one of: {allowed_values}. Received: {value}")
    return normalized


def _prompt_configure_value(
    prompt: str,
    *,
    allowed: tuple[str, ...],
    optional: bool = False,
) -> str | None:
    allowed_values = "|".join(allowed)
    prompt_text = f"{prompt} [{allowed_values}]"
    if optional:
        prompt_text = f"{prompt_text} (enter to skip)"

    while True:
        value = typer.prompt(prompt_text, default="" if optional else ..., show_default=False)
        normalized = value.strip().lower()
        if optional and not normalized:
            return None
        if normalized in allowed:
            return normalized
        typer.echo(f"Valor inválido. Escolha: {allowed_values}", err=True)


def _validate_configure_combination(
    *,
    privacy: str,
    preferred_mode: str | None,
    cloud: str | None,
    infra: str | None,
) -> None:
    if privacy != "restricted":
        return
    if infra == "remote":
        raise typer.BadParameter("privacy=restricted is incompatible with infra=remote")
    if preferred_mode == "remote-infra":
        raise typer.BadParameter(
            "privacy=restricted is incompatible with preferred_mode=remote-infra"
        )
    if cloud == "ok":
        raise typer.BadParameter("privacy=restricted is incompatible with cloud=ok")


@app.command()
def doctor(
    stale_days: Annotated[
        int,
        typer.Option(
            "--stale-days",
            help="Threshold (days) after which an activity is reported as stale.",
        ),
    ] = 7,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Interactive: prompt to apply suggested fixes. Requires TTY.",
        ),
    ] = False,
    ci: Annotated[
        bool,
        typer.Option(
            "--ci",
            help="JSON output to stdout, exit 0 always (for CI pipelines).",
        ),
    ] = False,
) -> None:
    """Inspeciona ambiente local e recomenda o modo operacional mais seguro.

    Three modes (dec-114):
      - default: read-only diagnostic, exit code reflects severity
      - --apply: interactive prompts for fixes (TTY required, never in CI)
      - --ci:    JSON output, exit 0 always
    """
    if apply and ci:
        typer.echo("Erro: --apply e --ci são mutuamente exclusivos.", err=True)
        raise typer.Exit(2)

    if apply:
        try:
            tty = os.isatty(0)
        except (OSError, ValueError):
            tty = False
        if not tty:
            typer.echo("Erro: --apply requer TTY interativo.", err=True)
            raise typer.Exit(1)

    if ci:
        from axon.doctor import run_all_checks
        from axon.doctor.formatters.json import format_results as json_format

        results = run_all_checks()
        typer.echo(json_format(results))
        raise typer.Exit(0)

    from axon.config.platform import build_doctor_report, detect_platform
    from axon.config.runtime import get_profile, get_runtime_sources, select_capabilities

    runtime = load_runtime_config()
    platform_config = detect_platform()
    profile_mode = None
    capability_selection = None
    if runtime.active_profile:
        try:
            profile = get_profile(runtime.active_profile)
            profile_mode = profile["mode"]
            capability_selection = select_capabilities(profile=profile)
        except ValueError:
            profile_mode = None
    report = build_doctor_report(
        runtime,
        platform_config,
        docker_available=shutil.which("docker") is not None,
        ollama_available=shutil.which("ollama") is not None,
        profile_mode=profile_mode,
        sources=get_runtime_sources(),
    )

    typer.echo("AXON doctor")
    typer.echo(f"platform: {report.platform}")
    typer.echo(f"configured_mode: {report.configured_mode or runtime.mode}")
    typer.echo(f"recommended_mode: {report.recommended_mode}")
    if report.active_profile:
        typer.echo(f"active_profile: {report.active_profile}")
    if report.profile_mode:
        typer.echo(f"profile_mode: {report.profile_mode}")
    if report.sources:
        typer.echo(f"mode_source: {report.sources.get('mode', 'unknown')}")
        typer.echo(f"engine_root_source: {report.sources.get('engine_root', 'unknown')}")
        typer.echo(f"vault_root_source: {report.sources.get('vault_root', 'unknown')}")
    typer.echo("checks:")
    for name, status in report.checks.items():
        typer.echo(f"- {name}: {status}")
    if report.notes:
        typer.echo("notes:")
        for note in report.notes:
            typer.echo(f"- {note}")
    if capability_selection is None and report.active_profile:
        try:
            capability_selection = select_capabilities(profile=get_profile(report.active_profile))
        except ValueError:
            capability_selection = None
    if capability_selection:
        typer.echo("capabilities:")
        typer.echo(f"- enabled: {', '.join(capability_selection.enabled_features) or '(none)'}")
        typer.echo(f"- overkill: {', '.join(capability_selection.overkill_features) or '(none)'}")

    # dec-114 capture/toolchain checks
    from axon.doctor import CheckStatus, max_severity, run_all_checks
    from axon.doctor.formatters.human import format_results as human_format

    results = run_all_checks()
    typer.echo("\ncapture checks (dec-114):")
    typer.echo(human_format(results))

    # presence + liveness (merged from the former axon.__main__ doctor)
    from datetime import UTC, datetime, timedelta
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(days=stale_days)

    def fmt_age(ts: datetime) -> str:
        delta = now - ts
        if delta.days >= 1:
            return f"{delta.days}d ago"
        hours = delta.seconds // 3600
        return f"{hours}h ago" if hours else "just now"

    presence_lines: list[str] = ["", "## Presence"]
    try:
        v = _pkg_version("axon-context-mcp")
    except PackageNotFoundError:
        v = "unknown"
    presence_lines.append(f"- axon: ok ({v})")

    presence_lines += ["", "## Liveness"]

    async def _latest_decision_ts():
        from axon.store.session_store import SessionStore

        store = SessionStore(_get_db_path())
        await store.init()
        try:
            # timestamptz as of 0005 - asyncpg returns a datetime already.
            return await store.latest_decision_ts()
        finally:
            await store.close()

    try:
        latest_dec_ts = asyncio.run(_latest_decision_ts())
        if latest_dec_ts is None:
            presence_lines.append(
                "- axon captures: none yet (commit something in an axon-init'd repo)"
            )
        else:
            if latest_dec_ts.tzinfo is None:
                latest_dec_ts = latest_dec_ts.replace(tzinfo=UTC)
            tag = "stale" if latest_dec_ts < stale_cutoff else "ok"
            presence_lines.append(f"- axon captures: {tag} (last {fmt_age(latest_dec_ts)})")
    except Exception as exc:
        presence_lines.append(f"- axon captures: error ({exc})")

    typer.echo("\n".join(presence_lines))

    severity = max_severity(results)
    if severity is CheckStatus.FAIL:
        raise typer.Exit(2)
    if severity is CheckStatus.WARN:
        raise typer.Exit(1)


@app.command()
def init(
    engine: Annotated[str, typer.Option("--engine", help="Diretório do engine AXON")],
    vault: Annotated[str, typer.Option("--vault", help="Diretório do vault externo")],
    mode: Annotated[str, typer.Option("--mode", help="Modo operacional")] = "full-local",
    force: Annotated[
        bool, typer.Option("--force", help="Sobrescreve .env.local existente")
    ] = False,
) -> None:
    """Gera scaffold inicial de `.env.local` para uma instalação nova."""
    from axon.config.platform import _to_dotenv, detect_platform

    normalized_mode = mode.strip().lower()
    if normalized_mode not in _RUNTIME_MODES:
        supported = ", ".join(_RUNTIME_MODES)
        raise typer.BadParameter(f"mode deve ser um de: {supported}")

    engine_root = Path(engine).expanduser()
    vault_root = Path(vault).expanduser()
    env_file = engine_root / ".env.local"

    if env_file.exists() and not force:
        typer.echo(f"Arquivo já existe: {env_file}. Use --force para sobrescrever.")
        raise typer.Exit(1)

    engine_root.mkdir(parents=True, exist_ok=True)
    vault_root.mkdir(parents=True, exist_ok=True)

    platform_payload = _to_dotenv(detect_platform())
    payload = (
        f"AXON_ENGINE={engine_root}\n"
        f"AXON_VAULT={vault_root}\n"
        f"AXON_RUNTIME_MODE={normalized_mode}\n"
        f"{platform_payload}"
    )
    env_file.write_text(payload, encoding="utf-8")
    config_file = engine_root / "axon.toml"
    config_file.write_text(
        "\n".join(
            [
                "[runtime]",
                f'mode = "{normalized_mode}"',
                'active_profile = "solo-dev"',
                f'engine_root = "{engine_root.as_posix()}"',
                f'vault_root = "{vault_root.as_posix()}"',
                "",
                "[profiles.solo-dev]",
                'description = "Single developer default"',
                f'mode = "{normalized_mode}"',
                "",
                "[profiles.team-dev]",
                'description = "Shared team setup"',
                'mode = "remote-infra"',
                "",
                "[profiles.privacy-first]",
                'description = "Prefer local or remote self-hosted paths"',
                'mode = "minimal"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    typer.echo(f"Scaffold criado: {env_file}")
    typer.echo(f"Config criado: {config_file}")
    typer.echo(f"mode: {normalized_mode}")
    typer.echo("Próximos passos:")
    typer.echo(f"1. source {env_file}")
    typer.echo("2. rode `axon doctor`")


@profile_app.command("list")
def profile_list() -> None:
    """Lista perfis conhecidos em `axon.toml`."""
    from axon.config.runtime import get_active_profile, list_profiles

    active = get_active_profile()
    profiles = list_profiles()
    if not profiles:
        typer.echo("Nenhum profile encontrado em axon.toml")
        raise typer.Exit(1)
    for name, description, mode in profiles:
        marker = "*" if name == active else "-"
        typer.echo(f"{marker} {name} | mode={mode} | {description}")


@profile_app.command("use")
def profile_use(
    name: Annotated[str, typer.Argument(help="Nome do profile")],
) -> None:
    """Define o profile ativo e sincroniza o modo no `axon.toml`."""
    from axon.config.runtime import use_profile

    try:
        use_profile(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Perfil ativo: {name}")


@profile_app.command("show")
def profile_show() -> None:
    """Exibe o profile ativo definido em `axon.toml`."""
    from axon.config.runtime import get_active_profile, get_profile, select_capabilities

    active = get_active_profile()
    if not active:
        typer.echo("Nenhum profile ativo em axon.toml")
        raise typer.Exit(1)
    try:
        profile = get_profile(active)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    capability_selection = select_capabilities(profile=profile)
    typer.echo(f"name: {profile['name']}")
    typer.echo(f"mode: {profile['mode']}")
    typer.echo(f"description: {profile['description']}")
    for field in ("cloud_policy", "infra_strategy", "memory_tier"):
        if profile.get(field):
            typer.echo(f"{field}: {profile[field]}")
    enabled_features = profile.get("enabled_features") or ()
    if enabled_features:
        typer.echo(f"enabled_features: {', '.join(enabled_features)}")
    typer.echo(
        f"selected_capabilities: {', '.join(capability_selection.enabled_features) or '(none)'}"
    )
    typer.echo(
        f"overkill_capabilities: {', '.join(capability_selection.overkill_features) or '(none)'}"
    )


@profile_app.command("create")
def profile_create(
    name: Annotated[str, typer.Argument(help="Nome do profile")],
    description: Annotated[str, typer.Option("--description", help="Descrição curta")],
    mode: Annotated[str, typer.Option("--mode", help="Modo operacional")],
    cloud_policy: Annotated[
        str | None, typer.Option("--cloud-policy", help="ok|avoid|deny")
    ] = None,
    infra_strategy: Annotated[
        str | None, typer.Option("--infra-strategy", help="local|remote")
    ] = None,
    memory_tier: Annotated[str | None, typer.Option("--memory-tier", help="light|full")] = None,
    enabled_features: Annotated[
        str | None, typer.Option("--enabled-features", help="Lista separada por vírgulas")
    ] = None,
) -> None:
    """Cria um novo profile simples em `axon.toml`."""
    from axon.config.runtime import create_profile

    normalized_cloud_policy = _normalize_configure_value(
        "cloud_policy", cloud_policy, allowed=_CONFIGURE_CLOUD_POLICIES
    )
    normalized_infra_strategy = _normalize_configure_value(
        "infra_strategy", infra_strategy, allowed=_CONFIGURE_INFRA_OPTIONS
    )
    normalized_memory_tier = _normalize_configure_value(
        "memory_tier", memory_tier, allowed=_CONFIGURE_MEMORY_OPTIONS
    )
    try:
        create_profile(
            name,
            description=description,
            mode=mode,
            cloud_policy=normalized_cloud_policy,
            infra_strategy=normalized_infra_strategy,
            memory_tier=normalized_memory_tier,
            enabled_features=tuple(
                feature.strip()
                for feature in (enabled_features or "").split(",")
                if feature.strip()
            ),
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Perfil criado: {name}")


@profile_app.command("export")
def profile_export(
    name: Annotated[str, typer.Argument(help="Nome do profile")],
) -> None:
    """Exporta um profile como snippet TOML."""
    from axon.config.runtime import export_profile

    try:
        typer.echo(export_profile(name))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def configure(
    use_case: Annotated[str | None, typer.Option("--use-case", help="solo|team|corporate")] = None,
    privacy: Annotated[
        str | None, typer.Option("--privacy", help="public|internal|confidential|restricted")
    ] = None,
    hardware: Annotated[
        str | None, typer.Option("--hardware", help="cpu-only|mac-laptop|nvidia|linux-workstation")
    ] = None,
    preferred_mode: Annotated[
        str | None,
        typer.Option("--preferred-mode", help="full-local|hybrid-local|remote-infra|minimal"),
    ] = None,
    cloud: Annotated[str | None, typer.Option("--cloud", help="ok|avoid|deny")] = None,
    infra: Annotated[str | None, typer.Option("--infra", help="local|remote")] = None,
    memory: Annotated[str | None, typer.Option("--memory", help="light|full")] = None,
) -> None:
    """Recomenda e aplica um profile mínimo com base em uso, privacidade e hardware."""
    from axon.config.runtime import recommend_profile, select_capabilities, use_profile

    interactive = use_case is None or privacy is None or hardware is None
    normalized_use_case = _normalize_configure_value(
        "use_case", use_case, allowed=_CONFIGURE_USE_CASES
    )
    normalized_privacy = _normalize_configure_value(
        "privacy", privacy, allowed=_CONFIGURE_PRIVACY_LEVELS
    )
    normalized_hardware = _normalize_configure_value(
        "hardware", hardware, allowed=_CONFIGURE_HARDWARE_OPTIONS
    )
    normalized_preferred_mode = _normalize_configure_value(
        "preferred_mode", preferred_mode, allowed=_RUNTIME_MODES
    )
    normalized_cloud = _normalize_configure_value("cloud", cloud, allowed=_CONFIGURE_CLOUD_POLICIES)
    normalized_infra = _normalize_configure_value("infra", infra, allowed=_CONFIGURE_INFRA_OPTIONS)
    normalized_memory = _normalize_configure_value(
        "memory", memory, allowed=_CONFIGURE_MEMORY_OPTIONS
    )

    if interactive:
        typer.echo("Configuração guiada")
        if normalized_use_case is None:
            normalized_use_case = _prompt_configure_value(
                "Caso de uso",
                allowed=_CONFIGURE_USE_CASES,
            )
        if normalized_privacy is None:
            normalized_privacy = _prompt_configure_value(
                "Privacidade",
                allowed=_CONFIGURE_PRIVACY_LEVELS,
            )
        if normalized_hardware is None:
            normalized_hardware = _prompt_configure_value(
                "Hardware",
                allowed=_CONFIGURE_HARDWARE_OPTIONS,
            )
        if normalized_preferred_mode is None:
            normalized_preferred_mode = _prompt_configure_value(
                "Modo preferido",
                allowed=_RUNTIME_MODES,
                optional=True,
            )
        if normalized_cloud is None:
            normalized_cloud = _prompt_configure_value(
                "Cloud",
                allowed=_CONFIGURE_CLOUD_POLICIES,
                optional=True,
            )
        if normalized_infra is None:
            normalized_infra = _prompt_configure_value(
                "Infra",
                allowed=_CONFIGURE_INFRA_OPTIONS,
                optional=True,
            )
        if normalized_memory is None:
            normalized_memory = _prompt_configure_value(
                "Memória",
                allowed=_CONFIGURE_MEMORY_OPTIONS,
                optional=True,
            )

    assert normalized_use_case is not None  # noqa: S101
    assert normalized_privacy is not None  # noqa: S101
    assert normalized_hardware is not None  # noqa: S101
    _validate_configure_combination(
        privacy=normalized_privacy,
        preferred_mode=normalized_preferred_mode,
        cloud=normalized_cloud,
        infra=normalized_infra,
    )

    profile_name, mode = recommend_profile(
        use_case=normalized_use_case,
        privacy=normalized_privacy,
        hardware=normalized_hardware,
        preferred_mode=normalized_preferred_mode,
        cloud=normalized_cloud,
        infra=normalized_infra,
        memory=normalized_memory,
    )
    capability_selection = select_capabilities(
        use_case=normalized_use_case,
        privacy=normalized_privacy,
        hardware=normalized_hardware,
        preferred_mode=normalized_preferred_mode,
        cloud=normalized_cloud,
        infra=normalized_infra,
        memory=normalized_memory,
    )
    try:
        use_profile(profile_name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"recommended_profile: {profile_name}")
    typer.echo(f"recommended_mode: {mode}")
    typer.echo(
        f"selected_capabilities: {', '.join(capability_selection.enabled_features) or '(none)'}"
    )
    typer.echo(
        f"overkill_capabilities: {', '.join(capability_selection.overkill_features) or '(none)'}"
    )
    typer.echo("Próximos passos:")
    typer.echo("1. revise com `axon profile show`")
    typer.echo("2. valide ambiente com `axon doctor`")


# ---------------------------------------------------------------------------
# pb search
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# pb session
# ---------------------------------------------------------------------------


@session_app.callback(invoke_without_command=True)
def session_root(
    ctx_name: Annotated[str | None, typer.Option("--ctx", help=_CTX_HELP)] = None,
) -> None:
    """Inicia ou exibe sessão ativa."""
    if ctx_name is None:
        typer.echo("Nenhuma sessão ativa. Use: axon session --ctx <contexto>")
        return

    resolved = _resolve_ctx(ctx_name)
    typer.echo(f"Sessão iniciada: {resolved}")
    # Persiste no env local ou em arquivo de estado
    state_file = _RUNTIME.engine_root / ".session_state"
    state_file.write_text(resolved or "")
    typer.echo(f"Contexto {resolved} ativo. Sessão salva em {state_file}")


@session_app.command("note")
def session_note(
    text: Annotated[str, typer.Argument(help="Texto da nota")],
) -> None:
    """Adiciona uma nota livre à sessão atual."""
    from axon.core.repo_identity import repo_identity
    from axon.store.session_store import SessionNote, SessionStore

    project = repo_identity(os.getcwd())

    async def _note() -> None:
        db = _get_db_path()
        store = SessionStore(db)
        await store.init()
        note = SessionNote(project=project, body=text)
        await store.save_note(note)
        typer.echo(f"Nota salva em '{project}': {text[:60]}{'...' if len(text) > 60 else ''}")

    asyncio.run(_note())


@app.command("note")
def note(
    text: Annotated[str, typer.Argument(help="Texto da nota livre de sessão")],
) -> None:
    """Alias para axon session note."""
    session_note(text)


async def _save_session_memory_with_spool(
    mem: SessionMemory,
    *,
    success_msg: str | None = None,
) -> None:
    from axon.store.session_store import SessionStore

    try:
        db = _get_db_path()
        store = SessionStore(db)
        await store.init()
        await store.save_session_memory(mem)
        if success_msg:
            typer.echo(success_msg)
    except Exception as exc:
        typer.echo(f"[axon] store save falhou ({exc}); gravando spool.", err=True)
        try:
            from axon.store.pending import _pending_paths, write_pending

            payload = {
                "kind": "session_memory",
                "project": mem.project,
                "summary": mem.summary,
                "raw_turns": mem.raw_turns,
                "created_at": mem.created_at.isoformat(),
            }
            await write_pending(
                payload=payload,
                commit_hash="session",
                paths=_pending_paths(),
            )
        except Exception as spool_exc:
            typer.echo(f"[axon] spool write falhou ({spool_exc}).", err=True)
        return

    try:
        await store.drain_pending()
    except Exception as drain_exc:
        typer.echo(f"[axon] drain_pending: {drain_exc}", err=True)


@app.command("session-save")
@session_app.command("save")
def session_save(
    cwd: Annotated[str | None, typer.Option("--cwd", help="Working directory da sessão")] = None,
    transcript: Annotated[
        str | None, typer.Option("--transcript", help="Path do transcript JSON")
    ] = None,
) -> None:
    """Salva a session memory como digest (chamado pelo Stop/SessionEnd hook do Claude Code)."""
    from axon.core.repo_identity import is_git_repo, repo_identity
    from axon.memory.digest import digest_turns
    from axon.memory.transcript import parse_transcript_turns
    from axon.store.session_store import SessionMemory

    cwd = cwd or os.getcwd()
    if not is_git_repo(cwd):
        # Closeout F1: a basename key is a guess; refuse rather than guess.
        typer.echo(f"[axon] session-save: fora de um repositorio git ({cwd}), skip.", err=True)
        return
    project = repo_identity(cwd)

    async def _save() -> None:
        turns: list[dict[str, str]] = []

        # Claude Code delivers the path in the hook payload on stdin; the env
        # var is only a manual-invocation convenience and is never set by the
        # harness. Reading only the env var is why this never fired.
        transcript_path = transcript or os.environ.get("CLAUDE_TRANSCRIPT_PATH")
        if transcript_path and os.path.exists(transcript_path):
            turns = parse_transcript_turns(transcript_path)

        if len(turns) < 2:
            typer.echo(f"[axon] Sessão muito curta ({len(turns)} turns), skip.", err=True)
            return

        turns = turns[-50:]

        # Capture carries no LLM: measured 2026-10-15, an LLM summary of a real
        # session took 18 s and loaded litellm, and with no key it fell back to
        # this same digest anyway.
        summary = digest_turns(turns)
        if not summary.strip():
            typer.echo("[axon] Nada a gravar, skip.", err=True)
            return

        mem = SessionMemory(project=project, summary=summary, raw_turns=len(turns))
        await _save_session_memory_with_spool(
            mem,
            success_msg=f"[axon] Session memory salva: {project} ({len(turns)} turns)",
        )

    asyncio.run(_save())


# ---------------------------------------------------------------------------
# pb adr
# ---------------------------------------------------------------------------


@adr_app.command("list")
def adr_list(
    project: Annotated[str, typer.Option("--project", "-p", help="Nome do projeto")],
    ctx: Annotated[str | None, typer.Option("--ctx")] = None,
) -> None:
    """Lista ADRs de um projeto."""
    _resolve_ctx(ctx)

    async def _list() -> None:
        from axon.store.session_store import ADR_INFERRED_NOTICE, SessionStore

        db = _get_db_path()
        store = SessionStore(db)
        await store.init()
        adrs = await store.get_adrs(project)
        if not adrs:
            typer.echo(f"Nenhum ADR encontrado para projeto '{project}'.")
            return
        for adr in adrs:
            typer.echo(f"\n# {adr.title}")
            if getattr(adr, "provenance", "llm-inferred") != "human":
                typer.echo(f"  {ADR_INFERRED_NOTICE}")
            typer.echo(f"  Decisão:   {adr.decision}")
            typer.echo(f"  Racional:  {adr.rationale}")
            typer.echo(f"  Data:      {adr.created_at}")

    asyncio.run(_list())


@adr_app.command("add")
def adr_add(
    project: Annotated[str, typer.Option("--project", "-p")],
    title: Annotated[str | None, typer.Option("--title")] = None,
    ctx: Annotated[str | None, typer.Option("--ctx")] = None,
) -> None:
    """Adiciona um ADR. Abre editor se --title não informado."""
    import datetime

    from axon.store.session_store import ADR, SessionStore

    _resolve_ctx(ctx)

    if title is None:
        title = typer.prompt("Título do ADR")

    context_text = typer.prompt("Contexto (por que precisou decidir)")
    decision = typer.prompt("Decisão")
    rationale = typer.prompt("Racional (por que essa opção)")

    async def _add() -> None:
        db = _get_db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        store = SessionStore(db)
        await store.init()
        adr = ADR(
            project=project,
            title=title,
            context=context_text,
            decision=decision,
            rationale=rationale,
            provenance="human",
            created_at=datetime.datetime.now(datetime.UTC),
        )
        await store.save_adr(adr)
        typer.echo(f"ADR salvo: {title}")

    asyncio.run(_add())


@adr_app.command("sync")
def adr_sync(
    project: Annotated[
        str | None, typer.Option("--project", "-p", help="Projeto específico (default: todos)")
    ] = None,
) -> None:
    """Exporta ADRs do DB para arquivos Markdown no vault Obsidian."""
    import datetime
    import json

    from axon.store.session_store import ADR_INFERRED_NOTICE, SessionStore

    vault_root = _RUNTIME.vault_root

    # A pasta do vault decide o ctx: `infer_ctx_from_path` classifica pelo primeiro
    # componente do path relativo ao vault. Cravar "personal" mandava o ADR de um
    # projeto `work` para vault/personal/adrs/, onde a indexação o trata como
    # pessoal e ele passa a aparecer em busca sem ctx. Fora do manifesto, mantém o
    # destino anterior.
    #
    # Lê o JSON direto, e não `load_project_manifest`: o loader exige que TODO path
    # do manifesto exista no disco e falha inteiro quando um projeto foi movido —
    # o mapa ficaria vazio e todos os ADRs voltariam para "personal". Aqui só o par
    # nome->ctx importa, e um projeto movido ainda tem ADR para sincronizar.
    manifest_path = _RUNTIME.engine_root / "config" / "projects.json"
    ctx_by_project: dict[str, str] = {}
    if manifest_path.exists():
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            ctx_by_project = {
                str(p["name"]): str(p["ctx"])
                for p in raw.get("projects", [])
                if p.get("name") and p.get("ctx") in VALID_CONTEXTS
            }
        except Exception:  # noqa: S110
            pass

    async def _sync() -> None:
        db = _get_db_path()
        store = SessionStore(db)
        await store.init()

        if project:
            projects_to_sync = [project]
        else:
            projects_to_sync = await store.all_projects()

        if not projects_to_sync:
            typer.echo("Nenhum ADR encontrado.")
            return

        synced = 0
        for proj in projects_to_sync:
            adrs_dir = vault_root / ctx_by_project.get(proj, "personal") / "adrs"
            adrs_dir.mkdir(parents=True, exist_ok=True)
            adrs = await store.get_adrs(proj, limit=100)
            lines = [f"# ADRs — {proj}\n\n_Last synced: {datetime.date.today().isoformat()}_\n"]
            for adr in adrs:
                lines.append(f"\n## {adr.title}\n")
                if getattr(adr, "provenance", "llm-inferred") != "human":
                    lines.append(f"{ADR_INFERRED_NOTICE}\n")
                created = adr.created_at.strftime("%Y-%m-%d") if adr.created_at else "N/A"
                lines.append(f"**Data:** {created}\n")
                lines.append(
                    f"**Decisão:** {adr.decision}\n\n**Racional:** {adr.rationale}"
                    f"\n\n**Contexto:** {adr.context}\n\n---\n"
                )
            content = "\n".join(lines)
            out_path = adrs_dir / f"{proj}.md"
            out_path.write_text(content, encoding="utf-8")
            typer.echo(f"  {proj}: {len(adrs)} ADR(s) → {out_path}")
            synced += 1

        typer.echo(f"Sync concluído: {synced} projeto(s).")

    asyncio.run(_sync())


def _save_compact_summary(*, project: str, summary: str) -> None:
    """Persist a harness-produced compact summary as SessionMemory."""
    from axon.store.session_store import SessionMemory

    async def _save() -> None:
        mem = SessionMemory(project=project, summary=summary, raw_turns=0)
        await _save_session_memory_with_spool(mem)

    asyncio.run(_save())


@app.command("compact-hook")
def compact_hook() -> None:
    """PostCompact hook: persist the harness's own compact summary (stdin payload)."""
    from axon.core.repo_identity import repo_identity
    from axon.memory.transcript import last_compact_summary

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        typer.echo("[axon] compact-hook: payload ilegivel, skip.", err=True)
        return
    if not isinstance(payload, dict):
        return

    transcript_path = payload.get("transcript_path") or ""
    cwd = payload.get("cwd") or os.getcwd()
    if not transcript_path or not os.path.exists(transcript_path):
        typer.echo(f"[axon] compact-hook: sem transcript ({transcript_path!r}).", err=True)
        return
    try:
        summary = last_compact_summary(transcript_path)
        if not summary:
            typer.echo("[axon] compact-hook: sem compact summary, skip.", err=True)
            return
        project = repo_identity(cwd)
        _save_compact_summary(project=project, summary=summary)
        typer.echo(f"[axon] Compact summary salvo: {project}", err=True)
    except Exception as exc:  # a hook must never interrupt the agent
        typer.echo(f"[axon] compact-hook: {exc}", err=True)


@app.command("session-hook")
def session_hook() -> None:
    """Stop / SessionEnd hook: persist the session (payload on stdin).

    A CLI command rather than a script in ~/.local/bin: this ships with the
    package, is covered by the suite, and carries no venv path in a shebang -
    renaming a venv once silently killed the hooks in thirteen repos.
    """
    if os.environ.get("AXON_SESSION_HOOK_SKIP", "").lower() in ("1", "true", "yes", "on"):
        # Closeout F4: a forge lane marks its headless `codex exec` so its
        # SessionEnd does not land in the operator's session history.
        typer.echo("[axon] session-hook: AXON_SESSION_HOOK_SKIP set, skip.", err=True)
        return
    payload: dict = {}
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        typer.echo("[axon] session-hook: payload ilegivel, skip.", err=True)
        return
    if not isinstance(payload, dict):
        return

    transcript_path = payload.get("transcript_path") or ""
    cwd = payload.get("cwd") or os.getcwd()
    if not transcript_path or not os.path.exists(transcript_path):
        typer.echo(f"[axon] session-hook: sem transcript ({transcript_path!r}).", err=True)
        return
    from axon.hooks.session_end import run_session_end

    run_session_end(transcript_path, cwd)


@hooks_app.command("install")
def hooks_install(
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Mutate files. Without this, the command only previews.",
        ),
    ] = False,
    path: Annotated[
        str | None,
        typer.Option("--path", help="Repo path (default: cwd)."),
    ] = None,
) -> None:
    """Install AXON git hooks (dec-113).

    Detects the active hook toolchain (pre-commit framework / husky /
    none / custom) and emits an integration plan. ``--apply`` is
    required to mutate anything. Refuses to run with ``--apply`` in a
    non-interactive environment.
    """
    from axon.hooks.husky_integration import dry_run_message as husky_msg
    from axon.hooks.precommit_integration import (
        dry_run_message as pc_msg,
    )
    from axon.hooks.precommit_integration import (
        merge_into as pc_merge,
    )
    from axon.hooks.toolchain_detector import Toolchain, detect

    repo_root = Path(path or os.getcwd())
    if not (repo_root / ".git").exists():
        typer.echo(f"Erro: {repo_root} não é um repositório git.", err=True)
        raise typer.Exit(1)

    def _is_tty() -> bool:
        try:
            return os.isatty(0)
        except (OSError, ValueError):
            return False

    if apply and not _is_tty():
        typer.echo(
            "Erro: `--apply` requer TTY interativo. Use `axon hooks install` "
            "para preview, ou rode manualmente em um shell.",
            err=True,
        )
        raise typer.Exit(1)

    toolchain = detect(repo_root)

    if toolchain is Toolchain.PRE_COMMIT_FRAMEWORK:
        cfg = repo_root / ".pre-commit-config.yaml"
        if not cfg.exists():
            cfg = repo_root / ".pre-commit-config.yml"
        typer.echo(pc_msg(cfg))
        if apply:
            if pc_merge(cfg):
                typer.echo(f"\n[axon] Entries adicionados em {cfg}")
            else:
                typer.echo("\n[axon] AXON entries já presentes — nada a fazer.")
        return

    if toolchain is Toolchain.HUSKY:
        typer.echo(husky_msg())
        if apply:
            typer.echo(
                "\n[axon] husky requires manual paste — AXON refuses to "
                "mutate .husky/ silently. Copy the wrappers above."
            )
        return

    if toolchain is Toolchain.CUSTOM:
        typer.echo(
            "Custom hooks detected in .git/hooks/. AXON will not overwrite "
            "them. Either delete the custom hooks first, or integrate "
            "AXON manually using the snippet below:\n"
        )
        from axon.hooks.git_installer import HOOKS
        from axon.hooks.husky_integration import wrapper_text

        for event in HOOKS:
            typer.echo(f"# .git/hooks/{event}:")
            typer.echo(wrapper_text(event).rstrip())
            typer.echo("")
        return

    # Toolchain.NONE
    typer.echo(
        "No hook toolchain detected. AXON can either:\n"
        "  (a) write directly to .git/hooks/ (recommended for solo repos)\n"
        "  (b) you can install pre-commit framework first:\n"
        "      pip install pre-commit && pre-commit install\n"
    )
    if apply:
        from axon.hooks.git_installer import install_hooks

        installed = install_hooks(repo_root)
        typer.echo(f"[axon] Installed: {', '.join(installed) or '(none new)'}")
    else:
        typer.echo("Run again with --apply to write to .git/hooks/.")


@hooks_app.command("status")
def hooks_status() -> None:
    """Show the detected hook toolchain and current install state."""
    from axon.hooks.toolchain_detector import detect

    toolchain = detect()
    typer.echo(f"toolchain: {toolchain}")


@pending_app.command("drain")
def pending_drain() -> None:
    """Drain .axon/pending/ into the SessionStore (dec-112)."""
    from axon.store.session_store import SessionStore

    async def _drain() -> None:
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            result = await store.drain_pending()
            typer.echo(
                f"drain: processed={result.processed} "
                f"quarantined={result.quarantined} retried={result.retried}"
            )
        finally:
            await store.close()

    asyncio.run(_drain())


@app.command("seed-lessons")
def seed_lessons(
    corpus: Annotated[
        Path,
        typer.Option("--corpus", help="Corpus path (JSON, agent-errors.json schema)"),
    ] = _DEFAULT_LESSON_CORPUS_PATH,
) -> None:
    """Seed the lessons store from the agent-error corpus (issue #142).

    Idempotent: an unchanged entry is skipped, a changed entry is updated in
    place, and a new entry is inserted. Prints one status line per corpus entry.
    """
    from axon.lessons.seed import seed_corpus
    from axon.mcp import server

    async def _seed() -> None:
        store = server._get_lesson_store()
        await store.init()
        try:
            results = await seed_corpus(corpus, server.axon_record_lesson, store=store)
            for line in results:
                typer.echo(line)
        finally:
            await store.close()

    asyncio.run(_seed())


@pending_app.command("recover")
def pending_recover(
    id_: Annotated[
        str | None,
        typer.Option("--id", help="Specific quarantine file basename to retry."),
    ] = None,
) -> None:
    """Re-attempt a quarantined payload (dec-112)."""
    from axon.config.data_root import data_root

    root = data_root()
    q_dir = root / "pending-quarantine"
    pending_dir = root / "pending"
    if not q_dir.exists():
        typer.echo("Sem quarantine.")
        return
    files = sorted(q_dir.iterdir())
    if id_:
        files = [f for f in files if f.name.startswith(id_)]
    if not files:
        typer.echo(f"Nenhum match para --id={id_!r}")
        return
    from axon.store.pending import _ensure_spool_ignored, _pending_paths

    paths = _pending_paths()
    _ensure_spool_ignored(paths)
    pending_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        target = pending_dir / f.name.rsplit(".", 1)[0]
        os.replace(f, target)
        typer.echo(f"recovered: {f.name} → {target.name}")
    typer.echo(f"Recovered {len(files)} file(s); run `axon pending drain`.")


# ---------------------------------------------------------------------------
# pb graph
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# pb index
# ---------------------------------------------------------------------------


_LEGACY_VAULT_ROOT_HELP = "Absolute vault path on the machine that wrote the legacy rows"




@app.command()
def setup() -> None:
    """Wizard de configuração pós-clone. Execute uma vez após clonar o repositório."""
    from axon.cli.setup import run_setup
    from axon.config.runtime import get_axon_config_path

    config_path = get_axon_config_path()
    run_setup(config_path=config_path, vault_root=_RUNTIME.vault_root)


# ---------------------------------------------------------------------------
# pb activity
# ---------------------------------------------------------------------------


@activity_app.command("run")
def activity_run(
    harness: Annotated[str, typer.Option("--harness")],
    model: Annotated[str, typer.Option("--model")] = "Gemini 3.1 Pro (High)",
    workspace: Annotated[Path, typer.Option("--workspace")] = Path.cwd(),
    prompt: Annotated[list[str], typer.Argument()] = None,
) -> None:
    """Run one explicit AGY invocation and store its captured activity."""
    if harness != "agy":
        raise typer.BadParameter("Only --harness agy is supported")
    if not prompt:
        raise typer.BadParameter("Prompt is required")

    prompt_str = " ".join(prompt)


    from axon.activity.adapters.agy import build_agy_session
    from axon.activity.agy_runner import run_agy
    from axon.activity.repository import PostgresActivityRepository
    from axon.activity.service import ActivityService

    async def _run() -> None:
        result = run_agy(model=model, workspace=workspace, prompt=prompt_str)
        source_id = f"agy-{uuid.uuid4()}"
        adapter_res = build_agy_session(result, source_id=source_id)

        repo = PostgresActivityRepository(_RUNTIME.pg_url)
        try:
            await repo.ensure_schema()
            svc = ActivityService(repo)
            await svc.ingest_events(
                adapter_res.events, cursor=adapter_res.cursor, sessions=adapter_res.sessions
            )

            outcome = next(
                (e.outcome for e in adapter_res.events if e.kind == "terminal_output"), None
            )
            summary = {
                "session_id": adapter_res.sessions[0].session_id,
                "outcome": outcome,
                "coverage": adapter_res.sessions[0].coverage,
            }
            typer.echo(json.dumps(summary))
        finally:
            await repo.close()

    asyncio.run(_run())


@activity_app.command("import")
def activity_import(
    harness: Annotated[str, typer.Option("--harness", help="Source harness")],
    path: Annotated[Path, typer.Option("--path", help="Path to JSONL file")],
) -> None:
    """Import historical activity from a JSONL file."""
    if harness not in ("claude-code", "codex", "agy"):
        raise typer.BadParameter("harness must be claude-code, codex, or agy")
    if not path.is_file():
        raise typer.BadParameter(f"path not found: {path}")

    from axon.activity.export import event_to_jsonl_line
    from axon.activity.models import ActivityEvent
    from axon.activity.repository import PostgresActivityRepository
    from axon.activity.service import ActivityService

    async def _run() -> None:
        repo = PostgresActivityRepository(dsn=_RUNTIME.pg_url)
        await repo.ensure_schema()
        try:
            service = ActivityService(repo)
            lines_to_import = []
            with path.open("r", encoding="utf-8") as f:
                for text_line in f:
                    text_line = text_line.strip()
                    if not text_line:
                        continue
                    event_dict = json.loads(text_line)
                    event = ActivityEvent.model_validate(event_dict)
                    lines_to_import.append(event_to_jsonl_line(event))

            result = await service.import_events(lines_to_import)
            typer.echo(
                json.dumps({"imported": result.stored, "coverage_warnings": result.warnings})
            )
        finally:
            await repo.close()

    asyncio.run(_run())


@activity_app.command("collect")
def activity_collect() -> None:
    """Collect pending spool events into the Postgres database."""
    from axon.activity.repository import PostgresActivityRepository
    from axon.activity.service import ActivityService

    async def _run() -> None:
        repo = PostgresActivityRepository(dsn=_RUNTIME.pg_url)
        await repo.ensure_schema()
        try:
            service = ActivityService(repo)
            health = await service.health()
            typer.echo(
                json.dumps(
                    {
                        "pending_count": health.pending_count,
                        "pending_bytes": health.pending_bytes,
                        "stored_bytes": health.stored_bytes,
                        "latest_error": health.latest_error,
                        "compatibility_warnings": health.compatibility_warnings,
                    }
                )
            )
        finally:
            await repo.close()

    asyncio.run(_run())


@activity_app.command("export")
def activity_export(
    session_id: Annotated[str, typer.Option("--session", help="Session ID to export")],
) -> None:
    """Export the activity timeline of a session as JSONL."""
    from axon.activity.repository import PostgresActivityRepository
    from axon.activity.service import ActivityService

    async def _run() -> None:
        repo = PostgresActivityRepository(dsn=_RUNTIME.pg_url)
        await repo.ensure_schema()
        try:
            service = ActivityService(repo)
            async for line in service.export_activity(session_id):
                typer.echo(line)
        finally:
            await repo.close()

    asyncio.run(_run())


@activity_app.command("search")
def activity_search(
    query: Annotated[str, typer.Argument(help="Text to find in past conversations")],
    project: Annotated[str | None, typer.Option("--project", help="Filter by project")] = None,
    harness: Annotated[str | None, typer.Option("--harness", help="Filter by harness")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max hits to return")] = 10,
) -> None:
    """Search past conversations: session, date, project, role and a snippet, newest first."""
    # Resolved per call, not from the import-time _RUNTIME: that copy kept the
    # live DSN under the suite's monkeypatched one and the tests wrote to it.
    from axon.activity.conversations import search_conversations
    from axon.activity.repository import PostgresActivityRepository

    if harness and harness not in ("claude-code", "codex", "agy"):
        raise typer.BadParameter("harness must be claude-code, codex, or agy")

    async def _run() -> list[str]:
        repo = PostgresActivityRepository(dsn=load_runtime_config().pg_url)
        try:
            await repo.ensure_schema()
            return await search_conversations(
                repo, query, project=project, harness=harness, limit=limit
            )
        finally:
            await repo.close()

    hits = asyncio.run(_run())
    typer.echo("\n".join(hits) if hits else f"no conversation matching {query!r}.")


@activity_app.command("backfill")
def activity_backfill(
    claude_dir: Annotated[
        Path, typer.Option("--claude-dir", help="Claude Code transcripts root")
    ] = Path.home() / ".claude" / "projects",
    codex_dir: Annotated[
        Path, typer.Option("--codex-dir", help="Codex rollouts root")
    ] = Path.home() / ".codex" / "sessions",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Count only, write nothing")] = False,
) -> None:
    """Import every transcript (subagents included); safe to run again."""
    import asyncpg

    from axon.activity.importer import ImportResult, import_transcript
    from axon.activity.repository import PostgresActivityRepository

    paths = sorted(
        {p for root in (claude_dir, codex_dir) if root.is_dir() for p in root.rglob("*.jsonl")}
    )
    totals: dict[str, dict[str, int]] = {}

    async def _run() -> None:
        repo: PostgresActivityRepository | None = PostgresActivityRepository(
            load_runtime_config().pg_url
        )
        try:
            if dry_run:
                # Read-only probe: a dry run must not apply migrations either.
                await repo.get_cursor(harness="codex", source_id="")
            else:
                await repo.ensure_schema()
        except (OSError, asyncpg.PostgresError) as exc:
            if not dry_run:
                raise
            typer.echo(f"[axon] backfill: no database ({exc}); counting whole files", err=True)
            await repo.close()
            repo = None
        try:
            for path in paths:
                try:
                    res = await import_transcript(path, repo, dry_run=dry_run)
                except (OSError, ValueError, asyncpg.PostgresError) as exc:
                    # One unreadable file must not stop the rest; its cursor
                    # did not move, so a later run retries it.
                    typer.echo(f"[axon] backfill: {path}: {exc}", err=True)
                    res = ImportResult(path=path, harness=None, skipped="error")
                row = totals.setdefault(
                    res.harness or "unknown",
                    dict.fromkeys(("files", "sessions", "events", "redactions", "skipped"), 0),
                )
                row["files"] += 1
                row["skipped"] += res.skipped is not None
                row["sessions"] += res.sessions
                row["events"] += res.events
                row["redactions"] += res.redactions
        finally:
            if repo is not None:
                await repo.close()

    asyncio.run(_run())
    prefix = "[dry-run] " if dry_run else ""
    for harness, row in sorted(totals.items()):
        typer.echo(prefix + harness + ": " + ", ".join(f"{k}={v}" for k, v in row.items()))


@activity_app.command("show")
def activity_show(
    session_id: Annotated[str, typer.Option("--session", help="Session ID to show timeline for")],
) -> None:
    """Show the timeline of a session."""
    from axon.activity.repository import PostgresActivityRepository
    from axon.activity.service import ActivityService

    async def _run() -> None:
        repo = PostgresActivityRepository(dsn=_RUNTIME.pg_url)
        await repo.ensure_schema()
        try:
            service = ActivityService(repo)
            events = await service.get_session_timeline(session_id)
            typer.echo(json.dumps([e.model_dump(mode="json") for e in events]))
        finally:
            await repo.close()

    asyncio.run(_run())


@activity_app.command("link-commits")
def activity_link_commits(
    session_id: Annotated[str | None, typer.Option("--session", help="One stored session")] = None,
    all_sessions: Annotated[bool, typer.Option("--all", help="Every stored session")] = False,
) -> None:
    """Link the commits each stored session produced to that session (idempotent)."""
    from axon.activity.commit_links import link_all_commits, link_commits
    from axon.activity.repository import PostgresActivityRepository

    if (session_id is None) == (not all_sessions):
        raise typer.BadParameter("pass exactly one of --session ID or --all")

    async def _run() -> tuple[int, int]:
        repo = PostgresActivityRepository(dsn=_RUNTIME.pg_url)
        await repo.ensure_schema()
        try:
            if all_sessions:
                return await link_all_commits(repo)
            return 1, await link_commits(repo, session_id)
        finally:
            await repo.close()

    sessions, added = asyncio.run(_run())
    typer.echo(f"sessions: {sessions}  links added: {added}")


def why(
    target: Annotated[str, typer.Argument(help="A commit (hash, HEAD~1, ...) or a file path")],
) -> None:
    """Show the sessions, typed prompts and decisions behind a commit or a file's last commits."""
    from axon.activity.repository import PostgresActivityRepository
    from axon.activity.why import WhyError, explain
    from axon.store.session_store import SessionStore

    async def _run() -> str:
        repo = PostgresActivityRepository(dsn=_RUNTIME.pg_url)
        store = SessionStore(_get_db_path())
        await repo.ensure_schema()
        try:
            return await explain(repo, store, target, cwd=Path.cwd())
        finally:
            await repo.close()
            await store.close()

    try:
        typer.echo(asyncio.run(_run()))
    except WhyError as exc:
        typer.echo(f"axon why: {exc}", err=True)
        raise typer.Exit(1) from exc


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    app()


if __name__ == "__main__":
    main()
