from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Annotated

import typer

from prometheus.config.runtime import load_runtime_config
from prometheus.context.compression_quality import compression_quality_note
from prometheus.context.registry import VALID_CONTEXTS
from prometheus.context.rtk import RTKError, compress_text_with_rtk, rtk_binary_path

app = typer.Typer(
    name="pb",
    help="Prometheus CLI — segundo cérebro do Sammy",
    no_args_is_help=True,
)
adr_app = typer.Typer(help="Gerencia ADRs (Architectural Decision Records)")
session_app = typer.Typer(help="Gerencia sessão de contexto ativa")
career_app = typer.Typer(help="Comandos de carreira")
cost_app = typer.Typer(help="Exibe custo de uso de LLMs")
til_app = typer.Typer(help="TIL e HOW-TO — knowledge automation")
deep_app = typer.Typer(help="Sugestões de aprofundamento técnico")
expand_app = typer.Typer(help="Expansão manual com staging obrigatório")
memory_app = typer.Typer(help="Memória Mem0 / Neo4j")
graph_app = typer.Typer(help="Grafo estrutural Graphify / Neo4j")
profile_app = typer.Typer(help="Perfis de instalação e uso")

app.add_typer(adr_app, name="adr")
app.add_typer(session_app, name="session")
app.add_typer(career_app, name="career")
app.add_typer(cost_app, name="cost")
app.add_typer(til_app, name="til")
app.add_typer(deep_app, name="deep")
app.add_typer(expand_app, name="expand")
app.add_typer(memory_app, name="memory")
app.add_typer(graph_app, name="graph")
app.add_typer(profile_app, name="profile")

QDRANT_DEFAULT_URL = "http://localhost:6333"
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

    from prometheus.observability.compression_telemetry import (
        CompressionRecord,
        CompressionTelemetryStore,
    )
    from prometheus.router.compressor import caveman_compress_guarded

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
        engines.append("rtk")
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


def _handle_expand_expected_error(exc: FileNotFoundError | ValueError) -> None:
    if isinstance(exc, FileNotFoundError):
        typer.echo(f"Arquivo não encontrado: {exc}", err=True)
    else:
        typer.echo(f"Não foi possível concluir a operação: {exc}", err=True)
    raise typer.Exit(1)


def _neo4j_driver():
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise typer.BadParameter(
            "neo4j driver não instalado. Instale o extra graph: pip install -e '.[graph]'"
        ) from exc

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "local-password")
    return GraphDatabase.driver(uri, auth=(user, password))


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


def _run_neo4j_write(cypher: str) -> None:
    driver = _neo4j_driver()
    try:
        with driver.session() as session:
            session.run(cypher)
    finally:
        driver.close()


def _run_neo4j_read(cypher: str) -> list[dict]:
    driver = _neo4j_driver()
    try:
        with driver.session() as session:
            return [dict(record) for record in session.run(cypher)]
    finally:
        driver.close()


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
        f"Contexto Prometheus (ctx={ctx_label}):\n{compressed_context}\n\n"
        f"Solicitação do usuário: {query}"
    )

    executor = (
        "Você é o executor Codex de uma tarefa do plano.\n"
        "Execute APENAS task_id informado, respeitando acceptance_criteria e tests.\n"
        "Saída obrigatória: resumo de mudanças, arquivos alterados, comandos executados, "
        "resultados de teste, próximos passos.\n"
        "Se houver bloqueio, pare e reporte causa raiz com alternativa segura.\n\n"
        "Contexto Prometheus comprimido:\n"
        f"{compressed_context}\n\n"
        "Task a executar: <COLE_A_TASK_JSON_AQUI>"
    )

    local_knowledge = (
        "Você é um assistente local para preencher notas de knowledge com baixa alucinação.\n"
        "Objetivo: criar rascunho objetivo para arquivo vazio (TIL/HOW-TO), "
        "sem decisões arquiteturais finais.\n"
        "Formato: título, resumo, passos práticos, exemplos curtos, "
        "perguntas para aprofundamento.\n\n"
        f"Contexto Prometheus (ctx={ctx_label}):\n{compressed_context}\n\n"
        f"Tema alvo: {query}"
    )

    return planner, executor, local_knowledge


def _load_retrieval_profile() -> tuple[str | None, str, tuple[str, ...]]:
    from prometheus.config.runtime import get_active_profile, get_profile, select_capabilities

    active_profile = _RUNTIME.active_profile or get_active_profile()
    mode = _RUNTIME.mode
    capabilities: tuple[str, ...] = ()

    if active_profile:
        try:
            profile = get_profile(active_profile)
            profile_mode = str(profile.get("mode") or "").strip()
            if profile_mode:
                mode = profile_mode
            capabilities = tuple(select_capabilities(profile=profile).enabled_features)
        except ValueError:
            pass

    return active_profile, mode, capabilities


def _select_retrieval_strategy(query: str, ctx: str | None) -> tuple[object, str, str | None, str]:
    from prometheus.context.contracts import select_default_retrieval_strategy
    from prometheus.router.classifier import TaskType, classify_task_with_source

    task_type = TaskType.CODE_ANALYSIS
    try:
        task_type, _source = classify_task_with_source(query, ctx=ctx)
    except Exception:
        pass

    profile, mode, capabilities = _load_retrieval_profile()
    strategy = select_default_retrieval_strategy(
        task_type=task_type,
        profile=profile,
        mode=mode,
        capabilities=capabilities,
    )
    return strategy, str(task_type.value), profile, mode


def _build_context_pack(
    *,
    strategy,
    task_type: str,
    profile: str | None,
    mode: str,
    effective_ctx: str | None,
    hits: list[dict],
):
    from prometheus.context.contracts import ContextPack

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
    from prometheus.embedder.engine import EmbedderEngine
    from prometheus.store.vector_store import VectorStore

    store = VectorStore(url=_RUNTIME.qdrant_url)
    engine = EmbedderEngine()
    try:
        query_vector = engine.embed_one(query)
        hits = await store.search(
            query_vector=query_vector,
            collections=collections,
            language=language,
            top_k=top_k,
        )
    finally:
        await store.close()

    return hits


# ---------------------------------------------------------------------------
# pb ask
# ---------------------------------------------------------------------------


@app.command()
def ask(
    query: Annotated[str, typer.Argument(help="Pergunta ou task")],
    ctx: Annotated[
        str | None, typer.Option("--ctx", help=_CTX_HELP)
    ] = None,
    cwd: Annotated[
        str | None, typer.Option("--cwd", help="Diretório para detecção automática de contexto")
    ] = None,
    rtk_max_tokens: Annotated[
        int, typer.Option("--rtk-max-tokens", help="Budget de tokens para contexto comprimido")
    ] = _RUNTIME.rtk_max_tokens,
) -> None:
    """Consulta ao segundo cérebro — detecta contexto e roteia para o modelo adequado."""
    from prometheus.context.detector import ContextDetector
    from prometheus.store.collections import get_search_collections
    from prometheus.store.session_store import SessionStore

    resolved_ctx = _resolve_ctx(ctx)

    async def _ask() -> None:
        from prometheus.observability import TraceStore

        db = _get_db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        store = SessionStore(db)
        await store.init()
        trace_id = str(uuid.uuid4())
        trace = TraceStore(_RUNTIME).recorder(trace_id=trace_id, caller="cli", ctx=resolved_ctx)
        try:
            detector = ContextDetector(store)
            result = detector.detect(query, cwd=cwd or os.getcwd())
            effective_ctx = resolved_ctx or result.context
            strategy, task_type, profile, mode = _select_retrieval_strategy(query, effective_ctx)
            collections = get_search_collections(effective_ctx) if effective_ctx else list(strategy.contexts)
            hits = await _semantic_search_hits(
                query,
                collections=collections,
                top_k=strategy.max_segments,
            )
            trace.append_stage(
                "retrieval",
                ctx=effective_ctx,
                payload={
                    "strategy": strategy.name,
                    "task_type": task_type,
                    "profile": profile or "",
                    "mode": mode,
                    "hit_count": len(hits),
                },
            )
            pack = _build_context_pack(
                strategy=strategy,
                task_type=task_type,
                profile=profile,
                mode=mode,
                effective_ctx=effective_ctx,
                hits=hits,
            )

            typer.echo(f"Contexto detectado: {result.display}")
            typer.echo(f"Busca em ctx={effective_ctx} ({collections})")
            typer.echo(f"trace_id: {trace_id}")

            if not hits:
                typer.echo(_context_pack_summary(pack))
                typer.echo("\nNenhum contexto relevante encontrado.")
                return

            typer.echo("\nContexto relevante:")
            snippets: list[str] = []
            for i, hit in enumerate(hits[: len(pack.segments)], start=1):
                payload = hit.get("payload", {})
                file_path = payload.get("file_path", "<sem arquivo>")
                symbol = payload.get("symbol", "<sem símbolo>")
                score = hit.get("score", 0.0)
                content = str(payload.get("content", "")).strip().replace("\n", " ")
                preview = (content[:200] + "...") if len(content) > 200 else content
                snippets.append(preview)
                typer.echo(f"{i}. [{score:.4f}] {file_path} :: {symbol}")

            typer.echo("\nSíntese inicial:")
            typer.echo(
                "Baseado nos trechos recuperados, estes parecem ser os pontos mais relevantes "
                "para sua pergunta:"
            )
            for i, text in enumerate(snippets[:3], start=1):
                typer.echo(f"{i}) {text}")

            if strategy.enable_compression:
                (
                    compressed_context,
                    engine_name,
                    caveman_note,
                    rtk_note,
                    before_tokens,
                    after_tokens,
                    reduction_pct,
                ) = await _compress_context_pipeline(
                    pack.text,
                    max_tokens=rtk_max_tokens,
                    caller="cli",
                    ctx=effective_ctx,
                )
            else:
                compressed_context = pack.text
                engine_name = "disabled"
                caveman_note = f"strategy={strategy.name}"
                rtk_note = None
                before_tokens = _estimate_tokens(pack.text)
                after_tokens = before_tokens
                reduction_pct = 0.0
            trace.append_stage(
                "compression",
                ctx=effective_ctx,
                model=engine_name,
                payload={
                    "strategy": strategy.name,
                    "before_tokens": before_tokens,
                    "after_tokens": after_tokens,
                    "reduction_pct": round(reduction_pct, 1),
                    "compression_enabled": strategy.enable_compression,
                    "caveman_note": caveman_note or "",
                    "rtk_note": rtk_note or "",
                },
            )

            planner_prompt, executor_prompt, local_prompt = _build_planner_executor_prompts(
                query=query,
                compressed_context=compressed_context,
                ctx_name=effective_ctx,
            )

            typer.echo("\ncompression:")
            typer.echo(f"engine: {engine_name}")
            if caveman_note:
                typer.echo(f"caveman_note: {caveman_note}")
            if rtk_note:
                typer.echo(f"rtk_note: {rtk_note}")
            typer.echo(f"tokens aprox: {before_tokens} -> {after_tokens} (-{reduction_pct:.1f}%)")
            typer.echo(_context_pack_summary(pack))
            stale_notes = _staleness_notes(hits)
            if stale_notes:
                typer.echo("staleness:")
                for note in stale_notes:
                    typer.echo(f"- {note}")

            typer.echo("\nPrompt pronto — Claude (Planner):")
            typer.echo(planner_prompt)

            typer.echo("\nPrompt pronto — Codex (Executor):")
            typer.echo(executor_prompt)

            typer.echo("\nPrompt pronto — Local (Knowledge Draft):")
            typer.echo(local_prompt)
        finally:
            await store.close()

    asyncio.run(_ask())


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
        typer.echo("Instale com: brew install rtk")
        raise typer.Exit(1)

    typer.echo(f"RTK: instalado em {rtk_path}")
    version = subprocess.run([rtk_path, "--version"], capture_output=True, text=True)
    typer.echo((version.stdout or version.stderr or "versão indisponível").strip())

    show = subprocess.run([rtk_path, "init", "--show"], capture_output=True, text=True)
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
        typer.echo("RTK não instalado. Rode: brew install rtk")
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
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)
    typer.echo("RTK inicializado com sucesso.")


@app.command("rtk-proxy")
def rtk_proxy(
    command: Annotated[str, typer.Argument(help="Comando para executar via rtk proxy")],
) -> None:
    """Executa um comando via RTK proxy com saída compactada."""
    rtk_path = _rtk_binary_path()
    if not rtk_path:
        typer.echo("RTK não instalado. Rode: brew install rtk")
        raise typer.Exit(1)

    parts = shlex.split(command)
    if not parts:
        raise typer.BadParameter("comando vazio")

    cmd = [rtk_path, "proxy", *parts]
    typer.echo(f"Executando: {' '.join(cmd)}")
    result = subprocess.run(cmd, text=True)
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
    """Atalho para `pb git ...` com saída filtrada por RTK."""
    if not git_args:
        raise typer.BadParameter("informe ao menos um argumento, ex.: pb git status")
    rtk_proxy(f"git {' '.join(git_args)}")


@app.command()
def doctor() -> None:
    """Inspeciona ambiente local e recomenda o modo operacional mais seguro."""
    from prometheus.config.platform import build_doctor_report, detect_platform
    from prometheus.config.runtime import get_profile, get_runtime_sources, select_capabilities

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

    typer.echo("Prometheus doctor")
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


@app.command()
def init(
    engine: Annotated[str, typer.Option("--engine", help="Diretório do engine Prometheus")],
    vault: Annotated[str, typer.Option("--vault", help="Diretório do vault externo")],
    mode: Annotated[
        str, typer.Option("--mode", help="Modo operacional")
    ] = "full-local",
    force: Annotated[
        bool, typer.Option("--force", help="Sobrescreve .env.local existente")
    ] = False,
) -> None:
    """Gera scaffold inicial de `.env.local` para uma instalação nova."""
    from prometheus.config.platform import _to_dotenv, detect_platform

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
        f"PROMETHEUS_ENGINE={engine_root}\n"
        f"PROMETHEUS_VAULT={vault_root}\n"
        f"PROMETHEUS_RUNTIME_MODE={normalized_mode}\n"
        f"{platform_payload}"
    )
    env_file.write_text(payload, encoding="utf-8")
    config_file = engine_root / "prometheus.toml"
    config_file.write_text(
        "\n".join(
            [
                "[runtime]",
                f'mode = "{normalized_mode}"',
                'active_profile = "solo-dev"',
                f'engine_root = "{engine_root}"',
                f'vault_root = "{vault_root}"',
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
    typer.echo("2. rode `pb doctor`")
    typer.echo("3. indexe seu vault com `pb index ~/vault/knowledge --ctx knowledge`")


@profile_app.command("list")
def profile_list() -> None:
    """Lista perfis conhecidos em `prometheus.toml`."""
    from prometheus.config.runtime import get_active_profile, list_profiles

    active = get_active_profile()
    profiles = list_profiles()
    if not profiles:
        typer.echo("Nenhum profile encontrado em prometheus.toml")
        raise typer.Exit(1)
    for name, description, mode in profiles:
        marker = "*" if name == active else "-"
        typer.echo(f"{marker} {name} | mode={mode} | {description}")


@profile_app.command("use")
def profile_use(
    name: Annotated[str, typer.Argument(help="Nome do profile")],
) -> None:
    """Define o profile ativo e sincroniza o modo no `prometheus.toml`."""
    from prometheus.config.runtime import use_profile

    try:
        use_profile(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Perfil ativo: {name}")


@profile_app.command("show")
def profile_show() -> None:
    """Exibe o profile ativo definido em `prometheus.toml`."""
    from prometheus.config.runtime import get_active_profile, get_profile, select_capabilities

    active = get_active_profile()
    if not active:
        typer.echo("Nenhum profile ativo em prometheus.toml")
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
    """Cria um novo profile simples em `prometheus.toml`."""
    from prometheus.config.runtime import create_profile

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
    from prometheus.config.runtime import export_profile

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
        str | None, typer.Option("--preferred-mode", help="full-local|hybrid-local|remote-infra|minimal")
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
    from prometheus.config.runtime import recommend_profile, select_capabilities, use_profile

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

    assert normalized_use_case is not None
    assert normalized_privacy is not None
    assert normalized_hardware is not None
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
    typer.echo("1. revise com `pb profile show`")
    typer.echo("2. valide ambiente com `pb doctor`")


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
    from prometheus.store.collections import get_search_collections

    resolved_ctx = _resolve_ctx(ctx)
    strategy, task_type, profile, mode = _select_retrieval_strategy(query, resolved_ctx)
    collections = get_search_collections(resolved_ctx) if resolved_ctx else list(strategy.contexts)
    typer.echo(f"Buscando em: {collections}")

    async def _search() -> None:
        from prometheus.observability import TraceStore

        trace_id = str(uuid.uuid4())
        trace = TraceStore(_RUNTIME).recorder(trace_id=trace_id, caller="cli", ctx=resolved_ctx)
        hits = await _semantic_search_hits(
            query,
            collections=collections,
            language=language,
            top_k=min(top_k, strategy.max_segments),
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

        for i, hit in enumerate(hits[: len(pack.segments)], start=1):
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
        str | None, typer.Argument(help=_CTX_HELP)
    ] = None,
) -> None:
    """Inicia ou exibe sessão ativa."""
    if ctx_name is None:
        typer.echo("Nenhuma sessão ativa. Use: pb session <contexto>")
        return

    resolved = _resolve_ctx(ctx_name)
    typer.echo(f"Sessão iniciada: {resolved}")
    # Persiste no env local ou em arquivo de estado
    state_file = _RUNTIME.engine_root / ".session_state"
    state_file.write_text(resolved or "")
    typer.echo(f"Contexto {resolved} ativo. Sessão salva em {state_file}")


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
        from prometheus.store.session_store import SessionStore

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

    from prometheus.store.session_store import ADR, SessionStore

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


# ---------------------------------------------------------------------------
# pb career
# ---------------------------------------------------------------------------


@career_app.command("metrics")
def career_metrics() -> None:
    """Exibe métricas de carreira compiladas do vault."""
    vault = _RUNTIME.vault_root
    career_path = vault / "career"
    if not career_path.exists():
        typer.echo("Vault de carreira não encontrado. Configure PROMETHEUS_VAULT.")
        raise typer.Exit(1)
    typer.echo(f"[career metrics] Lendo de {career_path}...")
    typer.echo("Funcionalidade completa disponível após indexação inicial (pb index).")


@career_app.command("brief")
def career_brief(
    company: Annotated[str, typer.Argument(help="Nome da empresa")],
) -> None:
    """Gera brief de empresa para entrevista."""
    typer.echo(f"Gerando brief para: {company}")
    typer.echo("[brief] Requer MCP Gateway ativo — rode `docker compose up -d` primeiro.")


@career_app.command("interview")
def career_interview(
    topic: Annotated[str, typer.Argument(help="Tópico da entrevista")],
) -> None:
    """Puxa respostas relevantes de entrevistas anteriores."""
    typer.echo(f"Buscando experiências para: {topic}")
    typer.echo("[interview] Requer MCP Gateway ativo — rode `docker compose up -d` primeiro.")


# ---------------------------------------------------------------------------
# pb cost
# ---------------------------------------------------------------------------


@cost_app.callback(invoke_without_command=True)
def cost_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        cost_today()


@cost_app.command("today")
def cost_today() -> None:
    """Exibe custo total de hoje."""
    _show_cost("today")


@cost_app.command("week")
def cost_week(
    breakdown: Annotated[bool, typer.Option("--breakdown", help="Detalha por contexto")] = False,
) -> None:
    """Exibe custo total da semana."""
    _show_cost("week", breakdown=breakdown)


def _show_cost(period: str, breakdown: bool = False) -> None:
    # Langfuse expõe API de custo quando infra está up
    typer.echo(f"Custo {period}: $0.00 (Langfuse não iniciado ou sem dados)")
    if breakdown:
        typer.echo("  personal:   $0.00")
        typer.echo("  career:     $0.00")
        typer.echo("  knowledge:  $0.00")
        typer.echo("  work:       $0.00")


@cost_app.command("compression")
def cost_compression() -> None:
    """Exibe tokens economizados pelo pipeline caveman+RTK."""
    from prometheus.observability.compression_telemetry import CompressionTelemetryStore

    store = CompressionTelemetryStore()
    s = store.summary()
    if s["total_calls"] == 0:
        typer.echo("Sem dados de compressão ainda.")
        return
    typer.echo(f"Total de chamadas : {s['total_calls']}")
    typer.echo(f"Tokens antes      : {s['total_before_tokens']:,}")
    typer.echo(f"Tokens depois     : {s['total_after_tokens']:,}")
    typer.echo(f"Tokens economizados: {s['total_saved_tokens']:,}")
    typer.echo(f"Redução média     : {s['avg_reduction_pct']}%")
    typer.echo("Por engine:")
    for engine, count in s["by_engine"].items():
        typer.echo(f"  {engine}: {count}x")


# ---------------------------------------------------------------------------
# pb til
# ---------------------------------------------------------------------------


@til_app.callback(invoke_without_command=True)
def til_capture(
    ctx: typer.Context,
    text: Annotated[str | None, typer.Argument(help="Texto do TIL")] = None,
    tags: Annotated[str | None, typer.Option("--tags", help="Tags separadas por vírgula")] = None,
    list_pending: Annotated[
        bool, typer.Option("--list", "--list-pending", help="Lista TILs pendentes")
    ] = False,
    promote_today: Annotated[
        bool, typer.Option("--promote-today", help="Promove todos os TILs do dia")
    ] = False,
) -> None:
    """Captura TIL ou lista/promove TILs pendentes."""
    if ctx.invoked_subcommand is not None:
        return

    if promote_today:
        _do_promote_today()
        return

    if list_pending:
        _list_til_pending()
        return

    if text:
        _capture_til(text, tags)
    else:
        typer.echo("Use: pb til <texto> [--tags tag1,tag2] | --list | --promote-today")


def _capture_til(text: str, tags_str: str | None) -> None:
    import datetime

    vault = _RUNTIME.vault_root
    today = datetime.date.today().isoformat()
    tags = [t.strip() for t in (tags_str or "").split(",") if t.strip()]
    tags_yaml = f"[{', '.join(tags)}]" if tags else "[]"

    filename = f"til-{today}-{text[:30].lower().replace(' ', '-').replace('/', '-')}.md"
    # Salva no diretório daily do dia atual
    daily_dir = vault / "knowledge" / "daily" / today
    daily_dir.mkdir(parents=True, exist_ok=True)
    til_path = daily_dir / filename

    content = f"""---
tags: {tags_yaml}
created: {today}
type: til
promoted: false
---

# TIL: {text}

<!-- Adicione detalhes, código, contexto aqui -->
"""
    til_path.write_text(content)
    typer.echo(f"TIL salvo: {til_path}")


def _list_til_pending() -> None:
    vault = _RUNTIME.vault_root
    knowledge = vault / "knowledge"
    if not knowledge.exists():
        typer.echo("Vault não encontrado.")
        return

    pending = [f for f in knowledge.rglob("til-*.md") if "promoted: false" in f.read_text()]
    if not pending:
        typer.echo("Nenhum TIL pendente de promoção.")
        return
    typer.echo(f"{len(pending)} TIL(s) pendente(s):")
    for p in pending:
        typer.echo(f"  {p.relative_to(vault)}")


def _do_promote_today() -> None:
    try:
        from prometheus.vault.til_promoter import run as promote_run

        promote_run()
    except ImportError:
        typer.echo("[promote] til_promoter não disponível.")


# ---------------------------------------------------------------------------
# pb graph
# ---------------------------------------------------------------------------

@graph_app.command("index")
def graph_index(
    project: Annotated[str, typer.Option("--project", help="Project slug para namespace Neo4j")],
    repo: Annotated[str, typer.Option("--repo", help="Repo a indexar com Graphify")],
) -> None:
    """Indexa grafo estrutural via Graphify com namespace isolado por projeto."""
    from prometheus.store.graph_namespace import transform_cypher

    graphify_bin = shutil.which("graphify")
    if graphify_bin is None:
        typer.echo(
            "graphifyy não instalado. "
            "Instale com: pip install -e '.[graph]' ou pip install graphifyy",
            err=True,
        )
        raise typer.Exit(1)

    repo_path = Path(repo).expanduser()
    if not repo_path.exists():
        typer.echo(f"Repo não encontrado: {repo_path}", err=True)
        raise typer.Exit(1)

    result = subprocess.run(
        [graphify_bin, str(repo_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        typer.echo(result.stderr.strip() or "graphify falhou ao extrair Cypher.", err=True)
        raise typer.Exit(result.returncode)

    cypher = result.stdout.strip()
    if not cypher:
        typer.echo("graphify não retornou Cypher para enviar ao Neo4j.", err=True)
        raise typer.Exit(1)

    namespaced = transform_cypher(cypher, project)
    _run_neo4j_write(namespaced)
    typer.echo(f"Grafo indexado: project={project} repo={repo_path}")


@graph_app.command("neighbors")
def graph_neighbors(
    node: Annotated[str, typer.Argument(help="Nome/id/símbolo do nó")],
    project: Annotated[str, typer.Option("--project", help="Project slug do namespace")],
    depth: Annotated[int, typer.Option("--depth", help="Profundidade de vizinhança")] = 1,
) -> None:
    """Lista vizinhos de um nó no namespace Graphify do projeto."""
    from prometheus.store.graph_namespace import neighbors_query

    rows = _run_neo4j_read(neighbors_query(node, project, depth))
    if not rows:
        typer.echo("Nenhum vizinho encontrado.")
        return
    for row in rows:
        typer.echo(f"{row.get('node')} -> {row.get('neighbor')}")


@graph_app.command("path")
def graph_path(
    from_node: Annotated[str, typer.Argument(help="Nó de origem")],
    to_node: Annotated[str, typer.Argument(help="Nó de destino")],
    project: Annotated[str, typer.Option("--project", help="Project slug do namespace")],
) -> None:
    """Mostra o caminho mais curto entre dois nós no namespace do projeto."""
    from prometheus.store.graph_namespace import path_query

    rows = _run_neo4j_read(path_query(from_node, to_node, project))
    if not rows:
        typer.echo("Nenhum caminho encontrado.")
        return
    for row in rows:
        path = row.get("path")
        if isinstance(path, list):
            typer.echo(" -> ".join(str(item) for item in path))
        else:
            typer.echo(str(path))


@til_app.command("howto")
def til_to_howto(
    from_file: Annotated[str, typer.Option("--from", help="Arquivo TIL de origem")],
) -> None:
    """Converte um TIL específico em HOW-TO manualmente."""
    vault = _RUNTIME.vault_root
    til_path = vault / from_file if not Path(from_file).is_absolute() else Path(from_file)

    if not til_path.exists():
        typer.echo(f"Arquivo não encontrado: {til_path}")
        raise typer.Exit(1)

    try:
        from prometheus.vault.til_promoter import promote_to_howto

        howto_path = promote_to_howto(til_path)
        typer.echo(f"HOW-TO criado: {howto_path}")
    except ImportError:
        typer.echo("[howto] til_promoter não disponível.")


# ---------------------------------------------------------------------------
# pb deep
# ---------------------------------------------------------------------------


@deep_app.command("suggest")
def deep_suggest() -> None:
    """Analisa TILs da semana e sugere tópicos para aprofundamento."""

    async def _suggest() -> None:
        try:
            from prometheus.vault.deep_suggester import suggest_deep_topics

            suggestions = await suggest_deep_topics()
            if not suggestions:
                typer.echo("Nenhuma sugestão gerada (vault vazio ou Ollama não disponível).")
                return
            typer.echo(f"\n{len(suggestions)} sugestão(ões) de aprofundamento:\n")
            for i, s in enumerate(suggestions, 1):
                typer.echo(f"{i}. {s['suggested_title']}")
                typer.echo(f"   Por quê: {s['why']}")
                typer.echo("   Perguntas:")
                for q in s.get("starting_questions", []):
                    typer.echo(f"     - {q}")
                typer.echo()
        except Exception as e:
            typer.echo(f"[deep suggest] Erro: {e}")

    asyncio.run(_suggest())


@deep_app.command("list")
def deep_list() -> None:
    """Lista notas deep existentes no vault."""
    vault = _RUNTIME.vault_root
    deep_dir = vault / "knowledge" / "deep"
    if not deep_dir.exists():
        typer.echo("Diretório deep não encontrado no vault.")
        return
    notes = list(deep_dir.rglob("*.md"))
    if not notes:
        typer.echo("Nenhuma nota deep encontrada.")
        return
    typer.echo(f"{len(notes)} nota(s) deep:\n")
    for n in sorted(notes):
        typer.echo(f"  {n.relative_to(vault)}")


# ---------------------------------------------------------------------------
# pb expand
# ---------------------------------------------------------------------------


@expand_app.command("run")
def expand_run(
    ctx: Annotated[str, typer.Option("--ctx", help="Contexto alvo")],
    topic: Annotated[str, typer.Option("--topic", help="Tema para expansão manual")],
    fast: Annotated[
        bool, typer.Option("--fast", help="Limita coleta local para execução rápida")
    ] = False,
    allow_cloud: Annotated[
        bool, typer.Option("--allow-cloud", help="Permite uso cloud se policy e budget liberarem")
    ] = False,
) -> None:
    """Executa expansão manual e grava apenas em staging."""
    from prometheus.expansion.service import ExpansionService

    resolved_ctx = _resolve_ctx(ctx)
    if resolved_ctx is None:
        raise typer.BadParameter("--ctx é obrigatório")

    service = ExpansionService(_RUNTIME)
    staging_path = service.run(
        ctx=resolved_ctx,
        topic=topic,
        fast=fast,
        allow_cloud=allow_cloud,
    )
    draft = service.review(staging_path)
    typer.echo(f"Staging criado: {staging_path}")
    typer.echo("Nenhuma escrita foi feita no vault final.")
    typer.echo(ExpansionService.format_review(draft))


@expand_app.command("review")
def expand_review(
    staging_file: Annotated[str, typer.Argument(help="Arquivo markdown de staging")],
) -> None:
    """Exibe gate de revisão e caminho de publicação."""
    from prometheus.expansion.service import ExpansionService

    service = ExpansionService(_RUNTIME)
    try:
        draft = service.review(Path(staging_file))
    except (FileNotFoundError, ValueError) as exc:
        _handle_expand_expected_error(exc)
    typer.echo(ExpansionService.format_review(draft))


@expand_app.command("approve")
def expand_approve(
    staging_file: Annotated[str, typer.Argument(help="Arquivo markdown de staging")],
) -> None:
    """Publica um draft aprovado e reindexa o arquivo final."""
    from prometheus.expansion.service import ExpansionService

    service = ExpansionService(_RUNTIME)
    try:
        publish_path, reindex_status = service.approve(Path(staging_file))
    except (FileNotFoundError, ValueError) as exc:
        _handle_expand_expected_error(exc)
    typer.echo(f"Publicado: {publish_path}")
    if reindex_status == "reindex_ok":
        typer.echo("Reindex concluído para o arquivo publicado.")
    else:
        typer.echo("Reindex não executado; publicação concluída e pode ser indexada depois.")


@expand_app.command("reject")
def expand_reject(
    staging_file: Annotated[str, typer.Argument(help="Arquivo markdown de staging")],
) -> None:
    """Rejeita um draft sem tocar o vault final."""
    from prometheus.expansion.service import ExpansionService

    service = ExpansionService(_RUNTIME)
    try:
        rejected_path = service.reject(Path(staging_file))
    except (FileNotFoundError, ValueError) as exc:
        _handle_expand_expected_error(exc)
    typer.echo(f"Staging rejeitado: {rejected_path}")
    typer.echo("Vault final preservado.")


# ---------------------------------------------------------------------------
# pb index
# ---------------------------------------------------------------------------


@app.command()
def index(
    path: Annotated[
        str | None, typer.Argument(help="Caminho a indexar (default: vault inteiro)")
    ] = None,
    ctx: Annotated[str | None, typer.Option("--ctx")] = None,
) -> None:
    """Indexação one-shot do vault ou de um path específico."""
    resolved_ctx = _resolve_ctx(ctx)
    target = Path(path) if path else _RUNTIME.vault_root
    typer.echo(f"Indexando: {target} (ctx={resolved_ctx or 'auto'})")

    if not target.exists():
        typer.echo(f"Path não encontrado: {target}")
        raise typer.Exit(1)

    async def _index() -> None:
        from prometheus.embedder.engine import EmbedderEngine
        from prometheus.embedder.pipeline import index_path
        from prometheus.store.graph_store import GraphStore
        from prometheus.store.vector_store import VectorStore

        engine = EmbedderEngine()
        store = VectorStore(url=_RUNTIME.qdrant_url)
        graph_store = GraphStore(url=_RUNTIME.redis_url)

        try:
            await store.ensure_collections()
            await graph_store.connect()
            vault_root = _RUNTIME.vault_root
            indexed_files, total_chunks = await index_path(
                target,
                engine=engine,
                store=store,
                vault_root=vault_root,
                forced_ctx=resolved_ctx,
                graph_store=graph_store,
            )
        finally:
            await store.close()
            await graph_store.close()

        typer.echo(f"Indexação concluída: {indexed_files} arquivo(s), {total_chunks} chunk(s)")
        if indexed_files == 0:
            typer.echo("Nenhum arquivo suportado encontrado (.java/.py/.ts/.md/.txt)")

    asyncio.run(_index())


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
    from prometheus.config.projects import load_project_manifest
    from prometheus.embedder.pipeline import iter_supported_files

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
        from prometheus.embedder.engine import EmbedderEngine
        from prometheus.embedder.pipeline import index_path
        from prometheus.store.graph_store import GraphStore
        from prometheus.store.vector_store import VectorStore

        engine = EmbedderEngine()
        store = VectorStore(url=_RUNTIME.qdrant_url)
        graph_store = GraphStore(url=_RUNTIME.redis_url)

        try:
            await store.ensure_collections()
            await graph_store.connect()
            total_files = 0
            total_chunks = 0
            for entry in selected:
                indexed_files, chunks = await index_path(
                    entry.path,
                    engine=engine,
                    store=store,
                    vault_root=_RUNTIME.vault_root,
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

        typer.echo(f"Indexação dev concluída: {total_files} arquivo(s), {total_chunks} chunk(s)")

    asyncio.run(_index_dev())


@app.command()
def watch(
    path: Annotated[
        str | None, typer.Argument(help="Caminho para observar (default: vault inteiro)")
    ] = None,
    ctx: Annotated[str | None, typer.Option("--ctx")] = None,
) -> None:
    """Observa mudanças e reindexa arquivos suportados em tempo real."""
    resolved_ctx = _resolve_ctx(ctx)
    vault_root = _RUNTIME.vault_root
    target = Path(path) if path else vault_root

    if not target.exists():
        typer.echo(f"Path não encontrado: {target}")
        raise typer.Exit(1)

    typer.echo(f"Watcher ativo em: {target} (ctx={resolved_ctx or 'auto'})")
    typer.echo("Pressione Ctrl+C para encerrar.")

    async def _watch() -> None:
        from prometheus.embedder.engine import EmbedderEngine
        from prometheus.embedder.pipeline import index_path
        from prometheus.store.graph_store import GraphStore
        from prometheus.store.vector_store import VectorStore
        from prometheus.watcher.main import run_watcher

        engine = EmbedderEngine()
        store = VectorStore(url=_RUNTIME.qdrant_url)
        graph_store = GraphStore(url=_RUNTIME.redis_url)

        async def _on_file(changed_path: Path) -> None:
            indexed_files, total_chunks = await index_path(
                changed_path,
                engine=engine,
                store=store,
                vault_root=vault_root,
                forced_ctx=resolved_ctx,
                graph_store=graph_store,
            )
            if indexed_files > 0:
                typer.echo(f"[watch] Reindexado: {changed_path} ({total_chunks} chunk(s))")

        try:
            await store.ensure_collections()
            await graph_store.connect()
            await run_watcher(target, _on_file)
        finally:
            await store.close()
            await graph_store.close()

    try:
        asyncio.run(_watch())
    except KeyboardInterrupt:
        typer.echo("Watcher encerrado.")


# ---------------------------------------------------------------------------
# pb memory
# ---------------------------------------------------------------------------


@memory_app.command("smoke")
def memory_smoke(
    ctx: Annotated[str, typer.Option("--ctx", help="Contexto da memória")] = "knowledge",
    text: Annotated[
        str,
        typer.Option("--text", help="Texto curto para gravar e recuperar"),
    ] = "Prometheus Mem0 Neo4j smoke test",
) -> None:
    """Valida conexão Mem0 com Qdrant + Neo4j mantendo a barreira work."""

    async def _smoke() -> None:
        from prometheus.memory.mem0_tool import add_memory, get_memory

        memory_id = await add_memory(text, ctx=ctx)
        results = await get_memory(text, ctx=ctx)
        typer.echo(f"Memória gravada: {memory_id or '<sem id retornado>'}")
        typer.echo(f"Memórias recuperadas: {len(results)}")

    try:
        asyncio.run(_smoke())
    except PermissionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    app()


if __name__ == "__main__":
    main()
