from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from prometheus.store.collections import get_search_collections
from prometheus.store.session_store import ADR, SessionStore
from prometheus.store.vector_store import VectorStore
from prometheus.store.graph_store import GraphStore

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

CONTEXT_BUDGETS: dict[str, int] = {
    "claude-code": 8000,
    "copilot": 2000,
}

_ENGINE = Path(os.environ.get("PROMETHEUS_ENGINE", Path.home() / "dev/Prometheus"))
_DB_PATH = _ENGINE / "data" / "prometheus.db"
_QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

mcp = FastMCP("prometheus-context-engine")

# Stores são inicializados lazy no primeiro uso
_vector_store: VectorStore | None = None
_graph_store: GraphStore | None = None
_session_store: SessionStore | None = None


def _get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore(url=_QDRANT_URL)
    return _vector_store


def _get_graph_store() -> GraphStore:
    global _graph_store
    if _graph_store is None:
        _graph_store = GraphStore(url=_REDIS_URL)
    return _graph_store


def _get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _session_store = SessionStore(_DB_PATH)
    return _session_store


def _truncate(text: str, budget: int) -> str:
    """Trunca resposta para caber no budget de tokens (aprox. 4 chars/token)."""
    max_chars = budget * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [truncado para {budget} tokens]"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_code(
    query: str,
    ctx: str | None = None,
    language: str | None = None,
    caller: str = "claude-code",
) -> str:
    """
    Busca semântica no codebase indexado.

    ctx: personal | career | knowledge | work
    Para acessar work, ctx='work' é obrigatório e explícito.
    Sem ctx, busca em personal + career + knowledge.
    caller: claude-code | copilot (afeta budget de tokens retornados)
    """
    collections = get_search_collections(ctx)
    store = _get_vector_store()
    results = await store.search(
        query=query,
        collections=collections,
        language=language,
        top_k=10,
    )
    if not results:
        return "Nenhum resultado encontrado."

    # Formata resultados
    lines: list[str] = []
    for r in results[:5]:
        payload = r.payload or {}
        lines.append(f"### {payload.get('symbol', 'unknown')} ({payload.get('language', '?')})")
        lines.append(f"Arquivo: {payload.get('file_path', '?')}")
        lines.append(f"Score: {r.score:.3f}")
        lines.append("")

    budget = CONTEXT_BUDGETS.get(caller, 4000)
    return _truncate("\n".join(lines), budget)


@mcp.tool()
async def get_session_memory(
    project: str,
    caller: str = "claude-code",
) -> str:
    """
    Retorna o resumo comprimido das últimas sessões no projeto.
    """
    store = _get_session_store()
    await store.init()
    memories = await store.get_session_memory(project, limit=3)
    if not memories:
        return f"Nenhuma memória de sessão para projeto '{project}'."

    lines = [f"## Sessões anteriores — {project}\n"]
    for m in memories:
        lines.append(f"**{m.created_at}** ({m.raw_turns} turns)")
        lines.append(m.summary)
        lines.append("")

    budget = CONTEXT_BUDGETS.get(caller, 4000)
    return _truncate("\n".join(lines), budget)


@mcp.tool()
async def get_dependencies(
    symbol: str,
    caller: str = "claude-code",
) -> str:
    """
    Retorna o grafo de dependências de uma classe ou função.
    """
    store = _get_graph_store()
    await store.connect()
    deps = await store.get_deps(symbol)
    if not deps["calls"] and not deps["called_by"]:
        return f"Sem dependências indexadas para '{symbol}'."

    lines = [f"## Dependências de {symbol}\n"]
    if deps["calls"]:
        lines.append(f"**Chama:** {', '.join(deps['calls'])}")
    if deps["called_by"]:
        lines.append(f"**Chamado por:** {', '.join(deps['called_by'])}")

    budget = CONTEXT_BUDGETS.get(caller, 4000)
    return _truncate("\n".join(lines), budget)


@mcp.tool()
async def get_adrs(
    project: str,
    ctx: str | None = None,
    caller: str = "claude-code",
) -> str:
    """
    Retorna ADRs de um projeto.
    Projetos de work só acessíveis com ctx='work'.
    """
    _WORK_PROJECTS = {"avangrid"}
    if project.lower() in _WORK_PROJECTS and ctx != "work":
        return "Contexto de trabalho requer ctx='work' explícito."

    store = _get_session_store()
    await store.init()
    adrs = await store.get_adrs(project)
    if not adrs:
        return f"Nenhum ADR para projeto '{project}'."

    lines = [f"## ADRs — {project}\n"]
    for adr in adrs:
        lines.append(f"### {adr.title}")
        lines.append(f"**Decisão:** {adr.decision}")
        lines.append(f"**Racional:** {adr.rationale}")
        lines.append("")

    budget = CONTEXT_BUDGETS.get(caller, 4000)
    return _truncate("\n".join(lines), budget)


@mcp.tool()
async def save_adr(
    project: str,
    title: str,
    context: str,
    decision: str,
    rationale: str,
) -> str:
    """
    Persiste uma decisão arquitetural.
    Use quando tomar uma decisão de design relevante.
    """
    import datetime

    store = _get_session_store()
    await store.init()
    adr = ADR(
        project=project,
        title=title,
        context=context,
        decision=decision,
        rationale=rationale,
        created_at=datetime.datetime.now(datetime.UTC),
    )
    await store.save_adr(adr)
    return f"ADR salvo: '{title}' em projeto '{project}'."


@mcp.tool()
async def ask(
    query: str,
    cwd: str | None = None,
    ctx: str | None = None,
    caller: str = "claude-code",
) -> str:
    """
    Ponto de entrada unificado. Detecta contexto, roteia modelo e busca contexto relevante.
    ctx: força contexto; sem ctx usa ContextDetector automático.
    """
    from prometheus.context.detector import ContextDetector

    session_store = _get_session_store()
    await session_store.init()

    detector = ContextDetector(session_store)
    result = detector.detect(query, cwd=cwd)
    effective_ctx = ctx or result.context

    # Busca contexto relevante
    code_context = await search_code(query=query, ctx=effective_ctx, caller=caller)

    budget = CONTEXT_BUDGETS.get(caller, 4000)
    response = f"Contexto detectado: {result.display}\n\n"
    response += f"## Contexto relevante\n{code_context}"

    return _truncate(response, budget)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
