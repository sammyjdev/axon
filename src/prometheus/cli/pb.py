from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

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

app.add_typer(adr_app, name="adr")
app.add_typer(session_app, name="session")
app.add_typer(career_app, name="career")
app.add_typer(cost_app, name="cost")
app.add_typer(til_app, name="til")
app.add_typer(deep_app, name="deep")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db_path() -> Path:
    return Path(os.environ.get("PROMETHEUS_ENGINE", Path.home() / "dev/Prometheus")) / "data" / "prometheus.db"


def _resolve_ctx(ctx: str | None, require_work_confirmation: bool = True) -> str | None:
    if ctx == "work" and require_work_confirmation:
        confirmed = typer.confirm("Acesso ao contexto work requer confirmação. Continuar?")
        if not confirmed:
            raise typer.Abort()
    return ctx


# ---------------------------------------------------------------------------
# pb ask
# ---------------------------------------------------------------------------

@app.command()
def ask(
    query: Annotated[str, typer.Argument(help="Pergunta ou task")],
    ctx: Annotated[Optional[str], typer.Option("--ctx", help="Contexto: personal|career|knowledge|work")] = None,
    cwd: Annotated[Optional[str], typer.Option("--cwd", help="Diretório para detecção automática de contexto")] = None,
) -> None:
    """Consulta ao segundo cérebro — detecta contexto e roteia para o modelo adequado."""
    from prometheus.context.detector import ContextDetector
    from prometheus.store.session_store import SessionStore

    resolved_ctx = _resolve_ctx(ctx)

    async def _ask() -> None:
        db = _get_db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        async with SessionStore(db) as store:
            detector = ContextDetector(store)
            result = detector.detect(query, cwd=cwd or os.getcwd())
            effective_ctx = resolved_ctx or result.context
            typer.echo(f"Contexto detectado: {result.display}")
            typer.echo(f"Roteando query para ctx={effective_ctx}...")
            # Router + MCP são resolvidos em runtime quando infra está up
            typer.echo("[ask] Infra MCP não iniciada — rode `docker compose up -d` primeiro.")

    asyncio.run(_ask())


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
    typer.echo("[search] Qdrant não iniciado — rode `docker compose up -d` primeiro.")


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
    state_file = Path(os.environ.get("PROMETHEUS_ENGINE", ".")) / ".session_state"
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
        async with SessionStore(db) as store:
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
        async with SessionStore(db) as store:
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
    vault = Path(os.environ.get("PROMETHEUS_VAULT", Path.home() / "vault"))
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

    vault = Path(os.environ.get("PROMETHEUS_VAULT", Path.home() / "vault"))
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

    vault = Path(os.environ.get("PROMETHEUS_VAULT", Path.home() / "vault"))
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
    vault = Path(os.environ.get("PROMETHEUS_VAULT", Path.home() / "vault"))
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
    vault = Path(os.environ.get("PROMETHEUS_VAULT", Path.home() / "vault"))
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
# pb index
# ---------------------------------------------------------------------------

@app.command()
def index(
    path: Annotated[Optional[str], typer.Argument(help="Caminho a indexar (default: vault inteiro)")] = None,
    ctx: Annotated[Optional[str], typer.Option("--ctx")] = None,
) -> None:
    """Indexação one-shot do vault ou de um path específico."""
    resolved_ctx = _resolve_ctx(ctx)
    target = Path(path) if path else Path(os.environ.get("PROMETHEUS_VAULT", Path.home() / "vault"))
    typer.echo(f"Indexando: {target} (ctx={resolved_ctx or 'auto'})")

    if not target.exists():
        typer.echo(f"Path não encontrado: {target}")
        raise typer.Exit(1)

    async def _index() -> None:
        from prometheus.embedder.engine import EmbedderEngine
        from prometheus.embedder.pipeline import index_path
        from prometheus.store.vector_store import VectorStore

        engine = EmbedderEngine()
        store = VectorStore(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))

        try:
            await store.ensure_collections()
            vault_root = Path(os.environ.get("PROMETHEUS_VAULT", Path.home() / "vault"))
            indexed_files, total_chunks = await index_path(
                target,
                engine=engine,
                store=store,
                vault_root=vault_root,
                forced_ctx=resolved_ctx,
            )
        finally:
            await store.close()

        typer.echo(f"Indexação concluída: {indexed_files} arquivo(s), {total_chunks} chunk(s)")
        if indexed_files == 0:
            typer.echo("Nenhum arquivo suportado encontrado (.java/.py/.ts/.md/.txt)")

    asyncio.run(_index())


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()
