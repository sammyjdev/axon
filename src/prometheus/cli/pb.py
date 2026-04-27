from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from pathlib import Path
from typing import Annotated
from typing import Optional

import typer

from prometheus.config.runtime import load_runtime_config
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

app.add_typer(adr_app, name="adr")
app.add_typer(session_app, name="session")
app.add_typer(career_app, name="career")
app.add_typer(cost_app, name="cost")
app.add_typer(til_app, name="til")
app.add_typer(deep_app, name="deep")
app.add_typer(expand_app, name="expand")
app.add_typer(memory_app, name="memory")

QDRANT_DEFAULT_URL = "http://localhost:6333"
_RUNTIME = load_runtime_config()


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
    from prometheus.router.compressor import caveman_compress

    before_tokens = _estimate_tokens(text)
    caveman_out, caveman_note = await caveman_compress(text, max_tokens=max_tokens)
    compressed_context, rtk_note = _compress_with_rtk(caveman_out, max_tokens=max_tokens)

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


def _build_planner_executor_prompts(query: str, compressed_context: str, ctx_name: str | None) -> tuple[str, str, str]:
    ctx_label = ctx_name or "auto"

    planner = (
        "Você é o planner. Gere um plano executável para agentes Codex em paralelo.\n"
        "Retorne JSON válido com campos: goal, assumptions, tasks[].\n"
        "Cada task deve conter: task_id, objective, files, dependencies, acceptance_criteria, tests, risk, rollback.\n"
        "Não execute código. Não omita riscos.\n\n"
        f"Contexto Prometheus (ctx={ctx_label}):\n{compressed_context}\n\n"
        f"Solicitação do usuário: {query}"
    )

    executor = (
        "Você é o executor Codex de uma tarefa do plano.\n"
        "Execute APENAS task_id informado, respeitando acceptance_criteria e tests.\n"
        "Saída obrigatória: resumo de mudanças, arquivos alterados, comandos executados, resultados de teste, próximos passos.\n"
        "Se houver bloqueio, pare e reporte causa raiz com alternativa segura.\n\n"
        "Contexto Prometheus comprimido:\n"
        f"{compressed_context}\n\n"
        "Task a executar: <COLE_A_TASK_JSON_AQUI>"
    )

    local_knowledge = (
        "Você é um assistente local para preencher notas de knowledge com baixa alucinação.\n"
        "Objetivo: criar rascunho objetivo para arquivo vazio (TIL/HOW-TO), sem decisões arquiteturais finais.\n"
        "Formato: título, resumo, passos práticos, exemplos curtos, perguntas para aprofundamento.\n\n"
        f"Contexto Prometheus (ctx={ctx_label}):\n{compressed_context}\n\n"
        f"Tema alvo: {query}"
    )

    return planner, executor, local_knowledge


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
    ctx: Annotated[Optional[str], typer.Option("--ctx", help="Contexto: personal|career|knowledge|work")] = None,
    cwd: Annotated[Optional[str], typer.Option("--cwd", help="Diretório para detecção automática de contexto")] = None,
    rtk_max_tokens: Annotated[int, typer.Option("--rtk-max-tokens", help="Budget de tokens para contexto comprimido")] = _RUNTIME.rtk_max_tokens,
) -> None:
    """Consulta ao segundo cérebro — detecta contexto e roteia para o modelo adequado."""
    from prometheus.context.detector import ContextDetector
    from prometheus.store.collections import get_search_collections
    from prometheus.store.session_store import SessionStore

    resolved_ctx = _resolve_ctx(ctx)

    async def _ask() -> None:
        db = _get_db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        store = SessionStore(db)
        await store.init()

        detector = ContextDetector(store)
        result = detector.detect(query, cwd=cwd or os.getcwd())
        effective_ctx = resolved_ctx or result.context
        collections = get_search_collections(effective_ctx)
        hits = await _semantic_search_hits(query, collections=collections, top_k=5)

        typer.echo(f"Contexto detectado: {result.display}")
        typer.echo(f"Busca em ctx={effective_ctx} ({collections})")

        if not hits:
            typer.echo("\nNenhum contexto relevante encontrado.")
            return

        typer.echo("\nContexto relevante:")
        snippets: list[str] = []
        context_lines: list[str] = []
        for i, hit in enumerate(hits[:5], start=1):
            payload = hit.get("payload", {})
            file_path = payload.get("file_path", "<sem arquivo>")
            symbol = payload.get("symbol", "<sem símbolo>")
            score = hit.get("score", 0.0)
            content = str(payload.get("content", "")).strip().replace("\n", " ")
            preview = (content[:200] + "...") if len(content) > 200 else content
            snippets.append(preview)
            context_lines.append(f"[{score:.4f}] {file_path} :: {symbol} :: {preview}")
            typer.echo(f"{i}. [{score:.4f}] {file_path} :: {symbol}")

        typer.echo("\nSíntese inicial:")
        typer.echo("Baseado nos trechos recuperados, estes parecem ser os pontos mais relevantes para sua pergunta:")
        for i, text in enumerate(snippets[:3], start=1):
            typer.echo(f"{i}) {text}")

        raw_context = "\n".join(context_lines)
        (
            compressed_context,
            engine_name,
            caveman_note,
            rtk_note,
            before_tokens,
            after_tokens,
            reduction_pct,
        ) = await _compress_context_pipeline(
            raw_context,
            max_tokens=rtk_max_tokens,
            caller="cli",
            ctx=effective_ctx,
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

        typer.echo("\nPrompt pronto — Claude (Planner):")
        typer.echo(planner_prompt)

        typer.echo("\nPrompt pronto — Codex (Executor):")
        typer.echo(executor_prompt)

        typer.echo("\nPrompt pronto — Local (Knowledge Draft):")
        typer.echo(local_prompt)

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
    agent: Annotated[str, typer.Option("--agent", help="Agente alvo: claude|codex|copilot")] = "claude",
    auto_patch: Annotated[bool, typer.Option("--auto-patch/--interactive", help="Executa init sem perguntas interativas")] = True,
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
    git_args: Annotated[list[str], typer.Argument(help="Argumentos do git (ex.: status, diff, log -n 5)")],
) -> None:
    """Atalho para `pb git ...` com saída filtrada por RTK."""
    if not git_args:
        raise typer.BadParameter("informe ao menos um argumento, ex.: pb git status")
    rtk_proxy(f"git {' '.join(git_args)}")


# ---------------------------------------------------------------------------
# pb search
# ---------------------------------------------------------------------------

@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Query de busca semântica")],
    ctx: Annotated[Optional[str], typer.Option("--ctx", help="Contexto: personal|career|knowledge|work")] = None,
    language: Annotated[Optional[str], typer.Option("--lang", help="Filtrar por linguagem")] = None,
    top_k: Annotated[int, typer.Option("--top", help="Número de resultados")] = 5,
) -> None:
    """Busca semântica no vault. Sem --ctx exclui work automaticamente."""
    from prometheus.store.collections import get_search_collections

    resolved_ctx = _resolve_ctx(ctx)
    collections = get_search_collections(resolved_ctx)
    typer.echo(f"Buscando em: {collections}")

    async def _search() -> None:
        hits = await _semantic_search_hits(
            query,
            collections=collections,
            language=language,
            top_k=top_k,
        )

        if not hits:
            typer.echo("Nenhum resultado encontrado.")
            return

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

    asyncio.run(_search())


# ---------------------------------------------------------------------------
# pb session
# ---------------------------------------------------------------------------

@session_app.callback(invoke_without_command=True)
def session_root(
    ctx_name: Annotated[Optional[str], typer.Argument(help="Contexto: personal|career|knowledge|work")] = None,
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
    ctx: Annotated[Optional[str], typer.Option("--ctx")] = None,
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
    title: Annotated[Optional[str], typer.Option("--title")] = None,
    ctx: Annotated[Optional[str], typer.Option("--ctx")] = None,
) -> None:
    """Adiciona um ADR. Abre editor se --title não informado."""
    from prometheus.store.session_store import ADR, SessionStore
    import datetime

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
    text: Annotated[Optional[str], typer.Argument(help="Texto do TIL")] = None,
    tags: Annotated[Optional[str], typer.Option("--tags", help="Tags separadas por vírgula")] = None,
    list_pending: Annotated[bool, typer.Option("--list", "--list-pending", help="Lista TILs pendentes")] = False,
    promote_today: Annotated[bool, typer.Option("--promote-today", help="Promove todos os TILs do dia")] = False,
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
    import datetime

    vault = _RUNTIME.vault_root
    knowledge = vault / "knowledge"
    if not knowledge.exists():
        typer.echo("Vault não encontrado.")
        return

    pending = [
        f for f in knowledge.rglob("til-*.md")
        if "promoted: false" in f.read_text()
    ]
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
    ctx: Annotated[str, typer.Option("--ctx", help="Contexto alvo")] ,
    topic: Annotated[str, typer.Option("--topic", help="Tema para expansão manual")] ,
    fast: Annotated[bool, typer.Option("--fast", help="Limita coleta local para execução rápida")] = False,
    allow_cloud: Annotated[bool, typer.Option("--allow-cloud", help="Permite uso cloud se policy e budget liberarem")] = False,
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
    path: Annotated[Optional[str], typer.Argument(help="Caminho a indexar (default: vault inteiro)")] = None,
    ctx: Annotated[Optional[str], typer.Option("--ctx")] = None,
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
    project: Annotated[Optional[str], typer.Option("--project", help="Nome do projeto no manifesto")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Lista projetos/arquivos sem gravar")] = False,
    manifest: Annotated[
        Optional[str],
        typer.Option("--manifest", help="Manifesto JSON de projetos"),
    ] = None,
) -> None:
    """Indexa projetos de desenvolvimento cadastrados em manifesto explícito."""
    from prometheus.config.projects import load_project_manifest
    from prometheus.embedder.pipeline import iter_supported_files

    manifest_path = Path(manifest) if manifest else _RUNTIME.engine_root / "config" / "projects.json"
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
                    f"{entry.name}: {indexed_files} arquivo(s), {chunks} chunk(s) "
                    f"(ctx={entry.ctx})"
                )
        finally:
            await store.close()
            await graph_store.close()

        typer.echo(f"Indexação dev concluída: {total_files} arquivo(s), {total_chunks} chunk(s)")

    asyncio.run(_index_dev())


@app.command()
def watch(
    path: Annotated[Optional[str], typer.Argument(help="Caminho para observar (default: vault inteiro)")] = None,
    ctx: Annotated[Optional[str], typer.Option("--ctx")] = None,
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
