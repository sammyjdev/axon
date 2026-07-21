from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import typer

from axon.config.runtime import load_runtime_config
from axon.context.compression_quality import compression_quality_note
from axon.context.registry import VALID_CONTEXTS
from axon.context.rtk import RTKError, compress_text_with_rtk, rtk_binary_path

app = typer.Typer(
    name="axon",
    help="AXON CLI — segundo cérebro do Sammy",
    no_args_is_help=True,
)
adr_app = typer.Typer(help="Gerencia ADRs (Architectural Decision Records)")
session_app = typer.Typer(help="Gerencia sessão de contexto ativa")
graph_app = typer.Typer(help="Grafo estrutural de código (SQLite)")
profile_app = typer.Typer(help="Perfis de instalação e uso")
portability_app = typer.Typer(help="Importa e exporta bundles de portabilidade")
pending_app = typer.Typer(help="Gerencia o backlog .axon/pending/ (dec-112)")
hooks_app = typer.Typer(help="Instala hooks AXON (dec-113, opt-in com --apply)")

app.add_typer(adr_app, name="adr")
app.add_typer(session_app, name="session")
app.add_typer(graph_app, name="graph")
app.add_typer(profile_app, name="profile")
app.add_typer(portability_app, name="portability")
app.add_typer(pending_app, name="pending")
app.add_typer(hooks_app, name="hooks")

_MAX_CHUNK_INPUT_CHARS = 4_000
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


async def _open_file_cache() -> tuple[object, object]:
    """Open a FileCache backed by the configured backend (sqlite or postgres).

    Returns (FileCache, close-handle) - caller must await handle.close().
    """
    from axon.store.file_cache import make_file_cache

    return await make_file_cache(_RUNTIME)


@asynccontextmanager
async def _index_lock_guard(*, fatal: bool, label: str = "index"):
    """Hold the machine-wide index lock (on the data_root) for an index op.

    Every index entry point (index / index-dev / watch / scan / howto reindex /
    expansion publish) shares this one lock root so they mutually exclude on the
    same machine. Yields True when the lock was acquired; on contention it
    either exits the CLI cleanly (fatal=True) or yields False so a watcher/loop
    can skip without crashing (fatal=False).
    """
    from axon.store.index_lock import IndexLockError, acquire_index_lock

    acquired = False
    try:
        async with acquire_index_lock(_RUNTIME.data_root):
            acquired = True
            yield True
            return
    except IndexLockError as exc:
        if acquired:
            # The guarded body raised IndexLockError, not the acquisition -
            # re-raise instead of miscatching it as lock contention.
            raise
        if fatal:
            typer.echo(f"Outro indexador ja esta em execucao: {exc}")
            raise typer.Exit(1) from exc
        typer.echo(f"[{label}] index lock ocupado por outro processo - pulando")
    yield False


def _resolve_ctx(ctx: str | None, require_work_confirmation: bool = True) -> str | None:
    if ctx == "work" and require_work_confirmation:
        confirmed = typer.confirm("Acesso ao contexto work requer confirmação. Continuar?")
        if not confirmed:
            raise typer.Abort()
    return ctx


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _rtk_binary_path() -> str | None:
    return rtk_binary_path()


def _compress_with_rtk(text: str, max_tokens: int) -> tuple[str, str | None]:
    try:
        return compress_text_with_rtk(text, max_tokens=max_tokens), None
    except RTKError as exc:
        return text, str(exc)


async def _compress_context_pipeline(
    text: str,
    *,
    max_tokens: int,
    caller: str,
    ctx: str | None,
) -> tuple[str, str, str | None, str | None, int, int, float]:
    from datetime import UTC, datetime

    from axon.observability.compression_telemetry import (
        CompressionRecord,
        CompressionTelemetryStore,
    )
    from axon.router.compressor import caveman_compress_guarded

    before_tokens = _estimate_tokens(text)
    caveman_out, caveman_note = await caveman_compress_guarded(text, max_tokens=max_tokens)

    compressed_context, rtk_note = _compress_with_rtk(caveman_out, max_tokens=max_tokens)
    rtk_quality_note = compression_quality_note(text, compressed_context)
    if rtk_quality_note:
        compressed_context = caveman_out
        rtk_note = rtk_quality_note

    engines: list[str] = []
    if caveman_note is None:
        engines.append("caveman/phi3")
    if rtk_note is None:
        engines.append("rtkx")
    engine_name = "+".join(engines) if engines else "fallback"

    after_tokens = _estimate_tokens(compressed_context)
    reduction = max(0, before_tokens - after_tokens)
    reduction_pct = (reduction / before_tokens * 100) if before_tokens else 0.0

    CompressionTelemetryStore().append(
        CompressionRecord(
            ts=datetime.now(UTC).isoformat(),
            engine=engine_name,
            caller=caller,
            ctx=ctx,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            reduction_tokens=reduction,
            reduction_pct=round(reduction_pct, 1),
            kind="compression",
        )
    )

    return (
        compressed_context,
        engine_name,
        caveman_note,
        rtk_note,
        before_tokens,
        after_tokens,
        reduction_pct,
    )


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
        raise typer.BadParameter(
            f"{field} must be one of: {allowed_values}. Received: {value}"
        )
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


def _build_planner_executor_prompts(
    query: str, compressed_context: str, ctx_name: str | None
) -> tuple[str, str, str]:
    ctx_label = ctx_name or "auto"

    planner = (
        "Você é o planner. Gere um plano executável para agentes Codex em paralelo.\n"
        "Retorne JSON válido com campos: goal, assumptions, tasks[].\n"
        "Cada task deve conter: task_id, objective, files, dependencies, "
        "acceptance_criteria, tests, risk, rollback.\n"
        "Não execute código. Não omita riscos.\n\n"
        f"Contexto AXON (ctx={ctx_label}):\n{compressed_context}\n\n"
        f"Solicitação do usuário: {query}"
    )

    executor = (
        "Você é o executor Codex de uma tarefa do plano.\n"
        "Execute APENAS task_id informado, respeitando acceptance_criteria e tests.\n"
        "Saída obrigatória: resumo de mudanças, arquivos alterados, comandos executados, "
        "resultados de teste, próximos passos.\n"
        "Se houver bloqueio, pare e reporte causa raiz com alternativa segura.\n\n"
        "Contexto AXON comprimido:\n"
        f"{compressed_context}\n\n"
        "Task a executar: <COLE_A_TASK_JSON_AQUI>"
    )

    local_knowledge = (
        "Você é um assistente local para preencher notas de knowledge com baixa alucinação.\n"
        "Objetivo: criar rascunho objetivo para arquivo vazio (TIL/HOW-TO), "
        "sem decisões arquiteturais finais.\n"
        "Formato: título, resumo, passos práticos, exemplos curtos, "
        "perguntas para aprofundamento.\n\n"
        f"Contexto AXON (ctx={ctx_label}):\n{compressed_context}\n\n"
        f"Tema alvo: {query}"
    )

    return planner, executor, local_knowledge


def _select_retrieval_strategy(query: str, ctx: str | None) -> tuple[object, str, str | None, str]:
    from axon.mcp.server import _select_retrieval_strategy as select_retrieval_strategy

    return select_retrieval_strategy(query, ctx)


def _build_context_pack(
    *,
    strategy,
    task_type: str,
    profile: str | None,
    mode: str,
    effective_ctx: str | None,
    hits: list[dict],
):
    from axon.context.contracts import ContextPack

    contexts = (effective_ctx,) if effective_ctx else strategy.contexts
    segments: list[str] = []
    total_chars = 0

    for hit in hits[: strategy.max_segments]:
        payload = hit.get("payload", {})
        file_path = payload.get("file_path", "<sem arquivo>")
        symbol = payload.get("symbol", "<sem símbolo>")
        score = hit.get("score", 0.0)
        content = str(payload.get("content", "")).strip().replace("\n", " ")
        remaining = strategy.max_chars - total_chars
        if remaining <= 0:
            break
        compressor_content = content[: min(_MAX_CHUNK_INPUT_CHARS, remaining)]
        segment = f"[{score:.4f}] {file_path} :: {symbol} :: {compressor_content}".strip()
        if not segment:
            continue
        segments.append(segment)
        total_chars += len(segment)

    metadata = (
        ("ctx", effective_ctx or "auto"),
        ("hits", str(len(segments))),
        ("profile", profile or ""),
        ("mode", mode),
    )
    return ContextPack(
        strategy=strategy,
        task_type=task_type,
        profile=profile,
        mode=mode,
        contexts=contexts,
        segments=tuple(segments),
        metadata=metadata,
    )


def _context_pack_summary(pack) -> str:
    contexts = ",".join(pack.contexts) if pack.contexts else "auto"
    return (
        f"ContextPack: strategy={pack.strategy.name} task_type={pack.task_type} "
        f"segments={len(pack.segments)} contexts={contexts}"
    )


def _staleness_notes(hits: list[dict]) -> list[str]:
    notes: list[str] = []
    for hit in hits:
        staleness = hit.get("staleness") or {}
        if not isinstance(staleness, dict) or not staleness.get("is_stale"):
            continue
        payload = hit.get("payload", {})
        symbol = payload.get("symbol", "<sem símbolo>")
        replacement_id = staleness.get("replacement_id")
        reason = staleness.get("replacement_reason") or ",".join(staleness.get("reasons", []))
        note = f"{symbol} stale"
        if replacement_id:
            note += f" -> replacement={replacement_id}"
        if reason:
            note += f" ({reason})"
        notes.append(note)
    return notes


async def _semantic_search_hits(
    query: str,
    *,
    collections: list[str],
    language: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    from axon.embedder.engine import EmbedderEngine
    from axon.store.vector_store_factory import make_vector_store

    store = make_vector_store(_RUNTIME)
    engine = EmbedderEngine()
    try:
        query_vector = engine.embed_one(query)
        hits = await store.search(
            query_vector=query_vector,
            query=query,
            collections=collections,
            language=language,
            top_k=top_k,
        )
    finally:
        await store.close()

    return hits


@app.command()
def rtk(
    text: Annotated[str, typer.Argument(help="Texto a comprimir")],
    max_tokens: Annotated[int, typer.Option("--max-tokens", help="Budget de tokens alvo")] = 350,
) -> None:
    """Comprime texto com RTK binário para reduzir consumo de tokens."""
    compressed, rtk_note = _compress_with_rtk(text, max_tokens=max_tokens)
    if rtk_note:
        raise typer.BadParameter(f"RTK externo indisponível: {rtk_note}")
    before_tokens = _estimate_tokens(text)
    after_tokens = _estimate_tokens(compressed)
    reduction = max(0, before_tokens - after_tokens)
    reduction_pct = (reduction / before_tokens * 100) if before_tokens else 0.0

    typer.echo("RTK engine: external")
    typer.echo(f"RTK tokens aprox: {before_tokens} -> {after_tokens} (-{reduction_pct:.1f}%)")
    typer.echo("\nTexto comprimido:")
    typer.echo(compressed)


@app.command("rtk-status")
def rtk_status() -> None:
    """Mostra status da instalação RTK oficial e integração local."""
    rtk_path = _rtk_binary_path()
    if not rtk_path:
        typer.echo("RTK: não instalado")
        typer.echo("Instale com: axon rtk-install (ou brew install rtkx)")
        raise typer.Exit(1)

    typer.echo(f"RTK: instalado em {rtk_path}")
    version = subprocess.run(  # noqa: S603
        [rtk_path, "--version"], capture_output=True, text=True
    )
    typer.echo((version.stdout or version.stderr or "versão indisponível").strip())

    show = subprocess.run(  # noqa: S603
        [rtk_path, "init", "--show"], capture_output=True, text=True
    )
    if show.returncode == 0 and (show.stdout or "").strip():
        typer.echo("\nRTK init --show:")
        typer.echo(show.stdout.strip())
    else:
        typer.echo("RTK init --show indisponível ou sem configuração.")


@app.command("rtk-init")
def rtk_init(
    agent: Annotated[
        str, typer.Option("--agent", help="Agente alvo: claude|codex|copilot")
    ] = "claude",
    auto_patch: Annotated[
        bool,
        typer.Option("--auto-patch/--interactive", help="Executa init sem perguntas interativas"),
    ] = True,
) -> None:
    """Inicializa RTK oficial para o agente escolhido."""
    rtk_path = _rtk_binary_path()
    if not rtk_path:
        typer.echo("rtkx não instalado. Rode: axon rtk-install")
        raise typer.Exit(1)

    agent_name = agent.lower()
    cmd = [rtk_path, "init", "-g"]
    if agent_name == "codex":
        cmd.append("--codex")
    elif agent_name == "copilot":
        if auto_patch:
            cmd.append("--auto-patch")
        cmd.append("--copilot")
    elif agent_name == "claude":
        if auto_patch:
            cmd.append("--auto-patch")
    else:
        raise typer.BadParameter("agent deve ser claude, codex ou copilot")

    typer.echo(f"Executando: {' '.join(cmd)}")
    result = subprocess.run(cmd, text=True)  # noqa: S603
    if result.returncode != 0:
        raise typer.Exit(result.returncode)
    typer.echo("RTK inicializado com sucesso.")


@app.command("rtk-install")
def rtk_install_cmd(
    version: Annotated[
        str, typer.Option("--version", help="Release tag a instalar (default: latest)")
    ] = "latest",
    pre: Annotated[
        bool,
        typer.Option("--pre/--stable", help="Inclui prereleases ao resolver o latest"),
    ] = False,
) -> None:
    """Baixa o binário rtkx para ~/.axon/bin (sem precisar de toolchain Rust)."""
    from axon.context import rtk_bootstrap as boot

    try:
        tag = (
            version
            if version != "latest"
            else boot.resolve_latest_tag(include_prerelease=pre)
        )
    except boot.BootstrapError as exc:
        typer.echo(f"rtkx: falha ao resolver release ({exc})")
        raise typer.Exit(1) from exc

    typer.echo(f"Instalando rtkx {tag} de {boot.RTKX_REPO}...")
    try:
        path = boot.bootstrap_rtkx(tag)
    except boot.BootstrapError as exc:
        typer.echo(f"rtkx: instalação falhou ({exc})")
        raise typer.Exit(1) from exc

    rtk_binary_path.cache_clear()
    typer.echo(f"rtkx instalado em {path}")


@app.command("rtk-proxy")
def rtk_proxy(
    command: Annotated[str, typer.Argument(help="Comando para executar via rtk proxy")],
) -> None:
    """Executa um comando via RTK proxy com saída compactada."""
    rtk_path = _rtk_binary_path()
    if not rtk_path:
        typer.echo("rtkx não instalado. Rode: axon rtk-install")
        raise typer.Exit(1)

    parts = shlex.split(command)
    if not parts:
        raise typer.BadParameter("comando vazio")

    cmd = [rtk_path, "proxy", *parts]
    typer.echo(f"Executando: {' '.join(cmd)}")
    result = subprocess.run(cmd, text=True)  # noqa: S603
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


@app.command("run")
def run_proxy(
    command: Annotated[str, typer.Argument(help="Comando shell para executar via RTK proxy")],
) -> None:
    """Atalho para executar qualquer comando shell com RTK proxy."""
    rtk_proxy(command)


@app.command("git")
def git_proxy(
    git_args: Annotated[
        list[str], typer.Argument(help="Argumentos do git (ex.: status, diff, log -n 5)")
    ],
) -> None:
    """Atalho para `axon git ...` com saída filtrada por RTK."""
    if not git_args:
        raise typer.BadParameter("informe ao menos um argumento, ex.: axon git status")
    rtk_proxy(f"git {' '.join(git_args)}")


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
            typer.echo(
                "Erro: --apply requer TTY interativo.", err=True
            )
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
    typer.echo(f"vector_backend: {runtime.vector_backend}")
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

    # dec-114 capture/adr/toolchain checks
    from axon.doctor import CheckStatus, max_severity, run_all_checks
    from axon.doctor.formatters.human import format_results as human_format

    results = run_all_checks()
    typer.echo("\ncapture & adr checks (dec-114):")
    typer.echo(human_format(results))

    # RTK/caveman presence + liveness (merged from the former axon.__main__ doctor)
    import subprocess
    import sys
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

    from axon.context.rtk import rtk_binary_path

    rtk_path = rtk_binary_path()
    if rtk_path:
        try:
            rtk_v = subprocess.check_output(  # noqa: S603
                [rtk_path, "--version"], text=True, timeout=3
            ).strip()
        except Exception:
            rtk_v = "unknown"
        presence_lines.append(f"- rtkx: ok ({rtk_v}) [{rtk_path}]")
    else:
        presence_lines.append("- rtkx: not installed (run `axon rtk-install`)")

    try:
        from axon.router.compressor import caveman_compress  # noqa: F401

        presence_lines.append("- caveman engine: ok (axon.router.compressor)")
    except Exception as exc:
        presence_lines.append(f"- caveman engine: error ({exc})")

    presence_lines += ["", "## Liveness"]

    async def _latest_decision_ts():
        from axon.store.session_store import SessionStore

        store = SessionStore(_get_db_path())
        await store.init()
        try:
            ts = await store.latest_decision_ts()
            return datetime.fromisoformat(ts) if ts is not None else None
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

    try:
        from axon.observability.compression_telemetry import CompressionTelemetryStore

        tstore = CompressionTelemetryStore()
        records = tstore.load_all()
        if not records:
            presence_lines.append("- compression telemetry: none yet")
        else:
            latest = records[-1]
            latest_ts = datetime.fromisoformat(latest.ts)
            if latest_ts.tzinfo is None:
                latest_ts = latest_ts.replace(tzinfo=UTC)
            tag = "stale" if latest_ts < stale_cutoff else "ok"
            presence_lines.append(
                f"- compression telemetry: {tag} "
                f"({len(records)} records, last {fmt_age(latest_ts)})"
            )
            caveman_recent = [r for r in records[-50:] if r.engine.startswith("caveman/")]
            if caveman_recent:
                presence_lines.append(
                    f"- caveman engine activity: ok ({len(caveman_recent)} of last 50 records)"
                )
            else:
                presence_lines.append(
                    "- caveman engine activity: not seen in last 50 records "
                    "(compression may be falling back)"
                )
    except Exception as exc:
        presence_lines.append(f"- compression telemetry: error ({exc})")

    if sys.platform == "darwin":
        share_root = Path.home() / "Library" / "Application Support"
    else:
        share_root = Path.home() / ".local" / "share"
    rtk_db = share_root / "rtkx" / "history.db"
    for name in ("rtkx", "rtk"):
        candidate = share_root / name / "history.db"
        if candidate.exists():
            rtk_db = candidate
            break
    if rtk_db.exists():
        rtk_ts = datetime.fromtimestamp(rtk_db.stat().st_mtime, tz=UTC)
        tag = "stale" if rtk_ts < stale_cutoff else "ok"
        presence_lines.append(f"- rtkx activity: {tag} (history.db touched {fmt_age(rtk_ts)})")
    else:
        presence_lines.append(f"- rtkx activity: not found ({rtk_db})")

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
    mode: Annotated[
        str, typer.Option("--mode", help="Modo operacional")
    ] = "full-local",
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
    typer.echo("3. indexe seu vault (ver `axon --help` para o comando de indexação atual)")


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
    memory_tier: Annotated[
        str | None, typer.Option("--memory-tier", help="light|full")
    ] = None,
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
    use_case: Annotated[
        str | None, typer.Option("--use-case", help="solo|team|corporate")
    ] = None,
    privacy: Annotated[
        str | None, typer.Option("--privacy", help="public|internal|confidential|restricted")
    ] = None,
    hardware: Annotated[
        str | None, typer.Option("--hardware", help="cpu-only|mac-laptop|nvidia|linux-workstation")
    ] = None,
    preferred_mode: Annotated[
        str | None,
        typer.Option(
            "--preferred-mode", help="full-local|hybrid-local|remote-infra|minimal"
        ),
    ] = None,
    cloud: Annotated[
        str | None, typer.Option("--cloud", help="ok|avoid|deny")
    ] = None,
    infra: Annotated[
        str | None, typer.Option("--infra", help="local|remote")
    ] = None,
    memory: Annotated[
        str | None, typer.Option("--memory", help="light|full")
    ] = None,
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
    normalized_cloud = _normalize_configure_value(
        "cloud", cloud, allowed=_CONFIGURE_CLOUD_POLICIES
    )
    normalized_infra = _normalize_configure_value(
        "infra", infra, allowed=_CONFIGURE_INFRA_OPTIONS
    )
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


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Query de busca semântica")],
    ctx: Annotated[
        str | None, typer.Option("--ctx", help=_CTX_HELP)
    ] = None,
    language: Annotated[str | None, typer.Option("--lang", help="Filtrar por linguagem")] = None,
    top_k: Annotated[int, typer.Option("--top", help="Número de resultados")] = 5,
) -> None:
    """Busca semântica no vault. Sem --ctx exclui work automaticamente."""
    from axon.store.collections import get_search_collections

    resolved_ctx = _resolve_ctx(ctx)
    strategy, task_type, profile, mode = _select_retrieval_strategy(query, resolved_ctx)
    collections = get_search_collections(resolved_ctx) if resolved_ctx else list(strategy.contexts)
    typer.echo(f"Buscando em: {collections}")

    async def _search() -> None:
        from axon.observability import TraceStore

        trace_id = str(uuid.uuid4())
        trace = TraceStore(_RUNTIME).recorder(trace_id=trace_id, caller="cli", ctx=resolved_ctx)
        hits = await _semantic_search_hits(
            query,
            collections=collections,
            language=language,
            top_k=top_k,
        )
        trace.append_stage(
            "retrieval",
            ctx=resolved_ctx,
            payload={
                "strategy": strategy.name,
                "task_type": task_type,
                "profile": profile or "",
                "mode": mode,
                "hit_count": len(hits),
            },
        )

        if not hits:
            typer.echo("Nenhum resultado encontrado.")
            return

        pack = _build_context_pack(
            strategy=strategy,
            task_type=task_type,
            profile=profile,
            mode=mode,
            effective_ctx=resolved_ctx,
            hits=hits,
        )

        for i, hit in enumerate(hits, start=1):
            payload = hit.get("payload", {})
            file_path = payload.get("file_path", "<sem arquivo>")
            symbol = payload.get("symbol", "<sem símbolo>")
            chunk_type = payload.get("chunk_type", "<sem tipo>")
            score = hit.get("score", 0.0)
            content = str(payload.get("content", "")).strip().replace("\n", " ")
            preview = (content[:180] + "...") if len(content) > 180 else content

            typer.echo(f"\n{i}. score={score:.4f} | {file_path}")
            typer.echo(f"   symbol={symbol} | type={chunk_type}")
            if preview:
                typer.echo(f"   preview: {preview}")

        typer.echo(f"\n{_context_pack_summary(pack)}")
        if len(pack.segments) < len(hits):
            typer.echo(
                "note: ContextPack summary reflects the strategy budget, "
                "but all requested hits are shown above."
            )
        typer.echo(f"trace_id: {trace_id}")
        stale_notes = _staleness_notes(hits)
        if stale_notes:
            typer.echo("staleness:")
            for note in stale_notes:
                typer.echo(f"- {note}")

    asyncio.run(_search())


# ---------------------------------------------------------------------------
# pb session
# ---------------------------------------------------------------------------


@session_app.callback(invoke_without_command=True)
def session_root(
    ctx_name: Annotated[
        str | None, typer.Option("--ctx", help=_CTX_HELP)
    ] = None,
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
    from axon.store.session_store import SessionNote, SessionStore

    project = os.path.basename(os.getcwd())

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


@app.command("session-save")
@session_app.command("save")
def session_save(
    cwd: Annotated[str | None, typer.Option("--cwd", help="Working directory da sessão")] = None,
    transcript: Annotated[
        str | None, typer.Option("--transcript", help="Path do transcript JSON")
    ] = None,
) -> None:
    """Comprime e salva session memory (chamado pelo PostStop hook do Claude Code)."""
    import json

    from axon.memory.session_compressor import SessionCompressor
    from axon.store.session_store import SessionMemory, SessionStore

    project = os.path.basename(cwd or os.getcwd())

    async def _save() -> None:
        turns: list[dict[str, str]] = []

        transcript_path = transcript or os.environ.get("CLAUDE_TRANSCRIPT_PATH")
        if transcript_path and os.path.exists(transcript_path):
            try:
                data = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
                raw = data if isinstance(data, list) else data.get("messages", [])
                for item in raw:
                    role = item.get("role") or item.get("type", "")
                    content = item.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(
                            c.get("text", "") for c in content if isinstance(c, dict)
                        )
                    if role and content and role in ("user", "assistant"):
                        turns.append({"role": role, "content": str(content)})
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        if len(turns) < 2:
            typer.echo(f"[axon] Sessão muito curta ({len(turns)} turns), skip.", err=True)
            return

        turns = turns[-50:]

        compressor = SessionCompressor()
        for turn in turns:
            compressor.add_turn(turn["role"], turn["content"])

        try:
            summary = await compressor.compress()
        except Exception as e:
            typer.echo(f"[axon] Erro ao comprimir sessão: {e}", err=True)
            return

        db = _get_db_path()
        store = SessionStore(db)
        await store.init()
        mem = SessionMemory(project=project, summary=summary, raw_turns=len(turns))
        await store.save_session_memory(mem)
        typer.echo(f"[axon] Session memory salva: {project} ({len(turns)} turns)")

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
        from axon.store.session_store import SessionStore

        db = _get_db_path()
        store = SessionStore(db)
        await store.init()
        adrs = await store.get_adrs(project)
        if not adrs:
            typer.echo(f"Nenhum ADR encontrado para projeto '{project}'.")
            return
        for adr in adrs:
            typer.echo(f"\n# {adr.title}")
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

    from axon.store.session_store import SessionStore

    vault_root = _RUNTIME.vault_root
    adrs_dir = vault_root / "personal" / "adrs"
    adrs_dir.mkdir(parents=True, exist_ok=True)

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
            adrs = await store.get_adrs(proj, limit=100)
            lines = [f"# ADRs — {proj}\n\n_Last synced: {datetime.date.today().isoformat()}_\n"]
            for adr in adrs:
                lines.append(f"\n## {adr.title}\n")
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


@adr_app.command("hook")
def adr_hook_install(
    path: Annotated[
        str | None, typer.Option("--path", help="Path do repositório git (default: cwd)")
    ] = None,
) -> None:
    """[deprecated] Use ``axon hooks install --apply`` (dec-113)."""
    import warnings

    warnings.warn(
        "`axon adr hook` is deprecated. Use `axon hooks install --apply`. "
        "See dec-113 for the new diagnostic-first flow.",
        DeprecationWarning,
        stacklevel=2,
    )
    typer.echo(
        "[axon] `axon adr hook` is deprecated — use `axon hooks install --apply`."
    )
    # Backwards-compatible behaviour: keep installing the old single hook
    # so existing users don't break mid-upgrade.
    import stat
    repo_path = Path(path or os.getcwd())
    hooks_dir = repo_path / ".git" / "hooks"

    if not hooks_dir.exists():
        typer.echo(f"Erro: {repo_path} não é um repositório git.", err=True)
        raise typer.Exit(1)

    hook_script_path = Path(__file__).parent.parent / "templates" / "post_commit_hook.sh"
    hook_content = hook_script_path.read_text(encoding="utf-8")

    target = hooks_dir / "post-commit"
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if "axon" in existing.lower():
            typer.echo("Hook já instalado (axon detectado). Nada a fazer.")
            return
        target.write_text(existing.rstrip() + "\n\n" + hook_content, encoding="utf-8")
        typer.echo(f"Hook anexado ao post-commit existente: {target}")
    else:
        target.write_text(hook_content, encoding="utf-8")
        typer.echo(f"Hook instalado: {target}")

    target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


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
        from axon.hooks.husky_integration import wrapper_text
        for event in ("post-commit", "pre-push", "post-merge", "post-checkout"):
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


@adr_app.command("infer-commit")
def adr_infer_commit(
    project: Annotated[str, typer.Option("--project", "-p", help="Nome do projeto")],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Bypass dec-110 commit signal gate (arch:/decision:/trailer).",
        ),
    ] = False,
) -> None:
    """Infere decisão arquitetural do último commit e salva ADR se detectado.

    Per dec-110, inference only fires when the commit subject starts with
    ``arch:`` / ``decision:`` (Conventional Commits compatible) or carries
    an ``ADR-Decision:`` trailer. ``--force`` bypasses the gate for manual
    invocations.

    Delegates to :mod:`axon.adr.inference` so the post-commit hook can
    reuse the same logic (issue #15).
    """
    from axon.adr.inference import (
        InferenceStatus,
        run_for_head_async,
    )

    async def _run() -> None:
        result = await run_for_head_async(
            project=project, force=force
        )
        if result.status is InferenceStatus.SAVED_ADR:
            typer.echo(f"[axon] ADR salvo: {result.title}")
        elif result.status is InferenceStatus.GATE_FAILED:
            layer = (
                result.outcome.failed_layer if result.outcome else None
            )
            typer.echo(
                f"[axon] ADR rebaixado para draft ({layer}): {result.title}"
            )
        # NO_SIGNAL / LLM_UNAVAILABLE / LLM_NULL / LLM_PARSE_ERROR
        # are silent by design — hook-friendly.

    asyncio.run(_run())
    return


@adr_app.command("review")
def adr_review(
    dormant: Annotated[
        bool,
        typer.Option("--dormant", help="Include dormant drafts in listing."),
    ] = False,
    weak_passes: Annotated[
        bool,
        typer.Option("--weak-passes", help="Show recent weak-pass entries."),
    ] = False,
    promote: Annotated[
        str | None,
        typer.Option(
            "--promote",
            help="Promote draft with given commit_hash to SessionStore.",
        ),
    ] = None,
    project: Annotated[
        str,
        typer.Option(
            "--project", "-p",
            help="Project name used when promoting (defaults to 'axon').",
        ),
    ] = "axon",
) -> None:
    """Review the ADR draft pool (dec-111)."""
    from axon.adr.audit import read_audit
    from axon.adr.draft_pool import (
        DraftRecord,
        list_drafts,
        read_draft,
    )

    if promote:
        from axon.config.data_root import data_root
        from axon.store.session_store import ADR, SessionStore

        draft_dir = data_root() / "adr-draft"
        path = draft_dir / f"{promote}.md"
        if not path.exists():
            typer.echo(f"Draft '{promote}' não encontrado em {draft_dir}/.")
            raise typer.Exit(1)
        record: DraftRecord = read_draft(path)

        async def _promote() -> None:
            store = SessionStore(_get_db_path())
            await store.init()
            try:
                adr = ADR(
                    project=project,
                    title=record.title,
                    context=record.context,
                    decision=record.decision,
                    rationale=record.rationale,
                )
                await store.save_adr(adr)
                path.unlink()
                typer.echo(f"[axon] Draft promovido: {record.title}")
            finally:
                await store.close()

        asyncio.run(_promote())
        return

    drafts = list_drafts(include_dormant=dormant)
    if not drafts:
        typer.echo("Nenhum draft de ADR pendente.")
    else:
        typer.echo(f"=== Drafts ({len(drafts)}) ===")
        for record in drafts:
            tag = "[DORMANT]" if record.dormant else "[ACTIVE]"
            mode = " [STRUCTURAL]" if record.structural_mode else ""
            typer.echo(
                f"{tag}{mode} {record.commit_hash[:10]}  "
                f"{record.failed_layer:>10}  {record.title}"
            )

    if weak_passes:
        entries = read_audit(kinds=("weak_pass",))
        typer.echo(f"\n=== Weak passes ({len(entries)}) ===")
        for e in entries[-10:]:
            typer.echo(f"  {e.get('commit_hash', '')[:10]}  {e.get('title', '')}")


@adr_app.command("audit")
def adr_audit(
    since: Annotated[
        str | None,
        typer.Option("--since", help="Filter entries since ISO timestamp (e.g. 7d)."),
    ] = None,
    weak_passes: Annotated[
        bool, typer.Option("--weak-passes", help="Only show weak passes.")
    ] = False,
) -> None:
    """Show the ADR audit log (dec-111)."""
    from datetime import UTC
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from axon.adr.audit import read_audit

    since_dt = None
    if since is not None:
        if since.endswith("d") and since[:-1].isdigit():
            since_dt = _dt.now(UTC) - _td(days=int(since[:-1]))
        else:
            try:
                since_dt = _dt.fromisoformat(since)
            except ValueError:
                typer.echo(f"--since inválido: {since!r}")
                raise typer.Exit(1) from None

    kinds = ("weak_pass",) if weak_passes else ("rejection", "weak_pass")
    entries = read_audit(since=since_dt, kinds=kinds)
    if not entries:
        typer.echo("Nenhuma entrada no audit log.")
        return
    typer.echo(f"=== Audit ({len(entries)} entradas) ===")
    for e in entries:
        kind = e.get("kind", "?")
        layer = e.get("layer", "")
        sm = " STRUCT" if e.get("structural_mode") else ""
        typer.echo(
            f"  [{kind:>10}{sm}] {e.get('commit_hash', '')[:10]} "
            f"{layer:>10}  {e.get('title', '')}"
        )


@adr_app.command("validate-drafts")
def adr_validate_drafts() -> None:
    """Run L1-full against pending drafts (dec-111).

    L1-full is currently a stub (Fase 2d follow-up wires the tree-sitter
    graph). This command exposes the entry point so triggers can call
    it; the body updates ``last_l1_full_at`` on each draft to clear the
    ``stale-pending`` state surfaced by ``axon doctor``.
    """
    from datetime import UTC
    from datetime import datetime as _dt

    from axon.adr.draft_pool import list_drafts, write_draft
    from axon.adr.gates.l1 import l1_full

    drafts = list_drafts(include_dormant=False)
    if not drafts:
        typer.echo("Nenhum draft para revalidar.")
        return

    promoted = 0
    demoted = 0
    held = 0
    for record in drafts:
        passed, _details = l1_full(
            f"{record.title}\n{record.context}\n{record.decision}\n{record.rationale}",
            repo_root=Path.cwd(),
        )
        record.last_l1_full_at = _dt.now(UTC)
        if not passed:
            record.dormant = True
            demoted += 1
        else:
            held += 1
        write_draft(record)

    typer.echo(
        f"validate-drafts: promoted={promoted} demoted={demoted} held={held}"
    )


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
    pending_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        target = pending_dir / f.name.rsplit(".", 1)[0]
        os.replace(f, target)
        typer.echo(f"recovered: {f.name} → {target.name}")
    typer.echo(f"Recovered {len(files)} file(s); run `axon pending drain`.")


# ---------------------------------------------------------------------------
# pb graph
# ---------------------------------------------------------------------------

@graph_app.command("index")
def graph_index(
    repo: Annotated[str, typer.Option("--repo", help="Repo a indexar no grafo de código")],
) -> None:
    """Indexa símbolos e edges de um repo no grafo de código (SQLite)."""
    from axon.code.indexer import index_repo
    from axon.code.resolver import index_edges
    from axon.store.session_store import SessionStore

    repo_path = Path(repo).expanduser()
    if not repo_path.exists():
        typer.echo(f"Repo não encontrado: {repo_path}", err=True)
        raise typer.Exit(1)

    async def _index() -> tuple[int, int]:
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            symbols = await index_repo(repo_path, store=store)
            edges = await index_edges(repo_path, store=store)
            return len(symbols), len(edges)
        finally:
            await store.close()

    n_symbols, n_edges = asyncio.run(_index())
    typer.echo(f"Grafo indexado: {n_symbols} símbolos, {n_edges} edges — {repo_path}")


@graph_app.command("neighbors")
def graph_neighbors(
    node: Annotated[str, typer.Argument(help="Nome/id/símbolo do nó")],
    depth: Annotated[int, typer.Option("--depth", help="Profundidade de vizinhança")] = 1,
) -> None:
    """Lista vizinhos de um nó no grafo de código (SQLite)."""
    from axon.store.session_store import SessionStore

    async def _neighbors() -> list[dict[str, str]]:
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            subgraph = await store.query_subgraph(node, depth=depth)
            return subgraph["edges"]  # type: ignore[return-value]
        finally:
            await store.close()

    edges = asyncio.run(_neighbors())
    if not edges:
        typer.echo("Nenhum vizinho encontrado.")
        return
    for edge in edges:
        typer.echo(f"{edge['source']} -> {edge['target']}")


@graph_app.command("path")
def graph_path(
    from_node: Annotated[str, typer.Argument(help="Nó de origem")],
    to_node: Annotated[str, typer.Argument(help="Nó de destino")],
) -> None:
    """Mostra o caminho mais curto entre dois nós no grafo de código (SQLite)."""
    from axon.store.session_store import SessionStore

    async def _path() -> list[str] | None:
        store = SessionStore(_get_db_path())
        await store.init()
        try:
            return await store.shortest_path(from_node, to_node)
        finally:
            await store.close()

    path = asyncio.run(_path())
    if not path:
        typer.echo("Nenhum caminho encontrado.")
        return
    typer.echo(" -> ".join(path))


# ---------------------------------------------------------------------------
# pb index
# ---------------------------------------------------------------------------


@app.command("index-dev")
def index_dev(
    project: Annotated[
        str | None, typer.Option("--project", help="Nome do projeto no manifesto")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Lista projetos/arquivos sem gravar")
    ] = False,
    manifest: Annotated[
        str | None,
        typer.Option("--manifest", help="Manifesto JSON de projetos"),
    ] = None,
) -> None:
    """Indexa projetos de desenvolvimento cadastrados em manifesto explícito."""
    from axon.config.projects import load_project_manifest
    from axon.embedder.pipeline import iter_supported_files

    manifest_path = (
        Path(manifest) if manifest else _RUNTIME.engine_root / "config" / "projects.json"
    )
    try:
        projects = load_project_manifest(manifest_path)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Manifesto inválido: {exc}", err=True)
        raise typer.Exit(1)

    selected = [p for p in projects if p.enabled]
    if project:
        selected = [p for p in selected if p.name == project]
        if not selected:
            typer.echo(f"Projeto não encontrado ou desabilitado no manifesto: {project}", err=True)
            raise typer.Exit(1)

    if not selected:
        typer.echo("Nenhum projeto habilitado no manifesto.")
        return

    for entry in selected:
        if entry.ctx == "work":
            _resolve_ctx("work")

    if dry_run:
        typer.echo(f"Manifesto: {manifest_path}")
        for entry in selected:
            files = list(iter_supported_files(entry.path, languages=set(entry.languages)))
            typer.echo(
                f"{entry.name}: ctx={entry.ctx} path={entry.path} "
                f"languages={','.join(entry.languages)} files={len(files)}"
            )
        return

    async def _index_dev() -> None:
        from axon.embedder.engine import EmbedderEngine
        from axon.embedder.pipeline import index_path
        from axon.store.pg_symbol_deps import PostgresSymbolDeps
        from axon.store.vector_store_factory import make_vector_store

        engine = EmbedderEngine()
        store = make_vector_store(_RUNTIME)
        graph_store = PostgresSymbolDeps(dsn=_RUNTIME.pg_url)
        file_cache, db_conn = await _open_file_cache()

        try:
            await store.ensure_collections()
            await graph_store.ensure_schema()
            total_files = 0
            total_chunks = 0
            for entry in selected:
                async with _index_lock_guard(fatal=True):
                    indexed_files, chunks = await index_path(
                        entry.path,
                        engine=engine,
                        store=store,
                        vault_root=_RUNTIME.vault_root,
                        file_cache=file_cache,
                        forced_ctx=entry.ctx,
                        graph_store=graph_store,
                        languages=set(entry.languages),
                    )
                total_files += indexed_files
                total_chunks += chunks
                typer.echo(
                    f"{entry.name}: {indexed_files} arquivo(s), {chunks} chunk(s) (ctx={entry.ctx})"
                )
        finally:
            await store.close()
            await graph_store.close()
            await db_conn.close()

        typer.echo(f"Indexação dev concluída: {total_files} arquivo(s), {total_chunks} chunk(s)")

    asyncio.run(_index_dev())


@app.command("index-vault")
def index_vault(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Lista arquivos do vault sem gravar")
    ] = False,
) -> None:
    """Indexa o vault inteiro (ctx inferido por arquivo via infer_ctx_from_path).

    Sem isso, `pb index`/`pb watch` (removidos em be66a51 / dec-125) deixaram o
    vault sem nenhum ponto de reindexação: cada arquivo recebe seu ctx via
    infer_ctx_from_path (forced_ctx=None), exatamente como index_path já
    suporta - `work/` continua excluído por is_ctx_indexable.
    """
    from axon.embedder.pipeline import iter_supported_files

    vault_root = _RUNTIME.vault_root

    if dry_run:
        files = list(iter_supported_files(vault_root, languages={"markdown"}))
        typer.echo(f"vault_root: {vault_root}")
        typer.echo(f"files={len(files)}")
        return

    async def _index_vault() -> None:
        from axon.embedder.engine import EmbedderEngine
        from axon.embedder.pipeline import index_path
        from axon.store.pg_symbol_deps import PostgresSymbolDeps
        from axon.store.vector_store_factory import make_vector_store

        engine = EmbedderEngine()
        store = make_vector_store(_RUNTIME)
        graph_store = PostgresSymbolDeps(dsn=_RUNTIME.pg_url)
        file_cache, db_conn = await _open_file_cache()

        try:
            await store.ensure_collections()
            await graph_store.ensure_schema()
            async with _index_lock_guard(fatal=True):
                indexed_files, chunks = await index_path(
                    vault_root,
                    engine=engine,
                    store=store,
                    vault_root=vault_root,
                    file_cache=file_cache,
                    forced_ctx=None,
                    graph_store=graph_store,
                    languages={"markdown"},
                )
        finally:
            await store.close()
            await graph_store.close()
            await db_conn.close()

        typer.echo(f"Vault indexado: {indexed_files} arquivo(s), {chunks} chunk(s)")

    asyncio.run(_index_vault())


# ---------------------------------------------------------------------------
# pb portability
# ---------------------------------------------------------------------------


@portability_app.command("export")
def portability_export(
    destination: Annotated[str, typer.Argument(help="Diretório do bundle exportado")],
) -> None:
    """Exporta config, stores e manifestos portáveis para um bundle."""
    from axon.portability.exporter import export_portability_bundle

    manifest = export_portability_bundle(Path(destination), runtime=_RUNTIME)
    typer.echo(f"Bundle exportado em: {Path(destination)}")
    typer.echo(f"Artefatos exportados: {len(manifest.artifacts)}")


@portability_app.command("import")
def portability_import(
    source: Annotated[str, typer.Argument(help="Diretório do bundle exportado")],
    engine_root: Annotated[str, typer.Argument(help="Novo engine root")],
) -> None:
    """Importa um bundle portátil para um engine root novo."""
    from axon.portability.importer import import_portability_bundle

    manifest = import_portability_bundle(Path(source), Path(engine_root))
    typer.echo(f"Bundle importado em: {Path(engine_root)}")
    typer.echo(f"Artefatos importados: {len(manifest.artifacts)}")


@app.command()
def scan(
    directory: Annotated[
        str | None, typer.Argument(help="Diretório a escanear (default: ~/dev)")
    ] = None,
    depth: Annotated[int, typer.Option("--depth", help="Profundidade máxima de busca")] = 2,
) -> None:
    """Auto-descobre repositórios git e atualiza o manifesto de projetos."""
    from axon.config.projects import ProjectEntry, write_project_manifest

    LANG_MAP = {
        ".py": "python",
        ".java": "java",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".md": "markdown",
        ".txt": "text",
    }

    scan_root = Path(directory or "~/dev").expanduser()
    if not scan_root.exists():
        typer.echo(f"Diretório não encontrado: {scan_root}", err=True)
        raise typer.Exit(1)

    manifest_path = _RUNTIME.engine_root / "config" / "projects.json"

    existing_names: set[str] = set()
    if manifest_path.exists():
        try:
            from axon.config.projects import load_project_manifest
            existing = load_project_manifest(manifest_path)
            existing_names = {e.name for e in existing}
        except Exception:  # noqa: S110
            pass

    def find_repos(base: Path, current_depth: int) -> list[Path]:
        repos = []
        if current_depth > depth:
            return repos
        try:
            for child in sorted(base.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    if (child / ".git").exists():
                        repos.append(child)
                    else:
                        repos.extend(find_repos(child, current_depth + 1))
        except PermissionError:
            pass
        return repos

    repos = find_repos(scan_root, 1)
    new_repos = [r for r in repos if r.name not in existing_names]

    if not new_repos:
        typer.echo("Nenhum repositório novo encontrado.")
        return

    typer.echo(f"Repositórios encontrados em {scan_root}:\n")

    def detect_language(repo: Path) -> str:
        counts: dict[str, int] = {}
        for f in repo.rglob("*"):
            if f.is_file() and f.suffix in LANG_MAP:
                lang = LANG_MAP[f.suffix]
                counts[lang] = counts.get(lang, 0) + 1
        return max(counts, key=lambda k: counts[k]) if counts else "python"

    def detect_ctx(repo: Path) -> str:
        if (repo / ".work").exists():
            return "work"
        return "personal"

    to_add: list[ProjectEntry] = []
    for repo in new_repos:
        lang = detect_language(repo)
        ctx = detect_ctx(repo)
        answer = typer.prompt(
            f"  [{repo.name}] {lang} ctx={ctx} — adicionar?",
            default="y",
        )
        if answer.lower() in ("y", "yes", "s", "sim"):
            to_add.append(
                ProjectEntry(
                    name=repo.name,
                    path=repo,
                    ctx=ctx,
                    enabled=True,
                    languages=(lang,),
                )
            )

    if not to_add:
        typer.echo("Nenhum repositório adicionado.")
        return

    write_project_manifest(manifest_path, to_add)
    typer.echo(f"\n{len(to_add)} repositório(s) adicionado(s) ao manifesto.")

    if typer.confirm("Indexar agora com axon index-dev?", default=True):
        for entry in to_add:
            typer.echo(f"Indexando {entry.name}...")

            async def _index_one(entry: ProjectEntry = entry) -> None:
                from axon.embedder.engine import EmbedderEngine
                from axon.embedder.pipeline import index_path
                from axon.store.pg_symbol_deps import PostgresSymbolDeps
                from axon.store.vector_store_factory import make_vector_store

                engine = EmbedderEngine()
                store = make_vector_store(_RUNTIME)
                graph_store = PostgresSymbolDeps(dsn=_RUNTIME.pg_url)
                file_cache, db_conn = await _open_file_cache()
                try:
                    await store.ensure_collections()
                    await graph_store.ensure_schema()
                    async with _index_lock_guard(fatal=True):
                        indexed, chunks = await index_path(
                            entry.path,
                            engine=engine,
                            store=store,
                            vault_root=_RUNTIME.vault_root,
                            file_cache=file_cache,
                            forced_ctx=entry.ctx,
                            graph_store=graph_store,
                        )
                        typer.echo(f"  {entry.name}: {indexed} arquivo(s), {chunks} chunk(s)")
                finally:
                    await store.close()
                    await graph_store.close()
                    await db_conn.close()

            asyncio.run(_index_one())


@app.command()
def setup() -> None:
    """Wizard de configuração pós-clone. Execute uma vez após clonar o repositório."""
    from axon.cli.setup import run_setup
    from axon.config.runtime import get_axon_config_path

    config_path = get_axon_config_path()
    run_setup(
        config_path=config_path,
        vault_root=_RUNTIME.vault_root,
        packs_root=_RUNTIME.engine_root / "domain-packs",
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    app()


if __name__ == "__main__":
    main()
