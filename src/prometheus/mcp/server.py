from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from prometheus.config.runtime import is_corporate_context, load_runtime_config
from prometheus.context.rtk import RTKError, compress_text_with_rtk
from prometheus.router.compressor import caveman_compress
from prometheus.embedder.engine import EmbedderEngine
from prometheus.observability.compression_telemetry import CompressionRecord, CompressionTelemetryStore
from prometheus.policy.core import PolicyRegistry
from prometheus.store.collections import get_search_collections
from prometheus.store.graph_store import GraphStore
from prometheus.store.session_store import ADR, SessionStore
from prometheus.store.vector_store import VectorStore

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

CONTEXT_BUDGETS: dict[str, int] = {
    "claude-code": 8000,
    "copilot": 2000,
}

_RUNTIME = load_runtime_config()
_POLICY = PolicyRegistry(_RUNTIME)
_DB_PATH = _RUNTIME.db_path
_QDRANT_URL = _RUNTIME.qdrant_url
_REDIS_URL = _RUNTIME.redis_url
_RTK_MAX_TOKENS = _RUNTIME.rtk_max_tokens
_COMPRESSION_TELEMETRY = CompressionTelemetryStore(_RUNTIME)

mcp = FastMCP("prometheus-context-engine")

# Stores são inicializados lazy no primeiro uso
_vector_store: VectorStore | None = None
_graph_store: GraphStore | None = None
_session_store: SessionStore | None = None
_embedder: EmbedderEngine | None = None


def _get_embedder() -> EmbedderEngine:
    global _embedder
    if _embedder is None:
        _embedder = EmbedderEngine()
    return _embedder


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


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def _compress_with_rtk(text: str, max_tokens: int) -> tuple[str, str | None]:
    try:
        return compress_text_with_rtk(text, max_tokens=max_tokens), None
    except RTKError as exc:
        return text, str(exc)


def _build_planner_executor_prompts(query: str, compressed_context: str, ctx_name: str | None) -> tuple[str, str, str]:
    ctx_label = ctx_name or "auto"

    planner = (
        "Você é o planner. Gere um plano executável para agentes Codex em paralelo.\n"
        "Retorne JSON válido com: goal, assumptions, tasks[].\n"
        "Cada task deve conter: task_id, objective, files, dependencies, acceptance_criteria, tests, risk, rollback.\n\n"
        f"Contexto Prometheus (ctx={ctx_label}):\n{compressed_context}\n\n"
        f"Solicitação: {query}"
    )
    executor = (
        "Você é o executor Codex. Execute apenas a task recebida.\n"
        "Saída obrigatória: mudanças, arquivos, comandos, testes, próximos passos.\n\n"
        f"Contexto Prometheus comprimido:\n{compressed_context}\n\n"
        "Task: <COLE_A_TASK_JSON_AQUI>"
    )
    local_knowledge = (
        "Você é um assistente local para preencher arquivos de knowledge vazios com rascunho útil.\n"
        "Formato: resumo, passos, exemplos curtos, perguntas para aprofundamento.\n\n"
        f"Contexto Prometheus (ctx={ctx_label}):\n{compressed_context}\n\n"
        f"Tema: {query}"
    )
    return planner, executor, local_knowledge


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_code(
    query: str,
    ctx: str | None = None,
    language: str | None = None,
    caller: str = "claude-code",
    max_depth: int = 2,
    max_nodes: int = 25,
    max_tokens: int = 1200,
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
    query_vector = _get_embedder().embed_one(query)
    results = await store.search(
        query_vector=query_vector,
        collections=collections,
        language=language,
        top_k=10,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_tokens=max_tokens,
    )
    if not results:
        return "Nenhum resultado encontrado."

    # Formata resultados
    lines: list[str] = []
    for r in results[:5]:
        payload = r.get("payload") or {}
        lines.append(f"### {payload.get('symbol', 'unknown')} ({payload.get('language', '?')})")
        lines.append(f"Arquivo: {payload.get('file_path', '?')}")
        lines.append(f"Score: {float(r.get('score', 0.0)):.3f}")
        lines.append("")

    top_symbol = ((results[0].get("payload") or {}).get("symbol") if results else None)
    if top_symbol:
        graph = _get_graph_store()
        await graph.connect()
        traversal = await graph.traverse(top_symbol, max_depth=max_depth, max_nodes=max_nodes)
        lines.append("## Dependencias relacionadas (2-step)")
        lines.append(f"Root: {traversal['root']}")
        lines.append(f"Nodes: {', '.join(traversal['nodes'][:10])}")

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
    memories = await store.get_session_memories(project, limit=3)
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
    deps = await store.get_subgraph(symbol)
    if not deps["exists"]:
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
    rtk_max_tokens: int | None = None,
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

    gateway_decision = _POLICY.decide(
        ctx=effective_ctx,
        model="claude-haiku-4-5-20251001",
        caller=caller,
        force_cloud=(caller == "cloud"),
    )
    if not gateway_decision.allowed and caller == "cloud":
        return (
            "Fallback cloud bloqueado por policy central. "
            f"reason_code={gateway_decision.reason_code.value}; policy_version={gateway_decision.policy_version}."
        )

    # Busca contexto relevante
    code_context = await search_code(query=query, ctx=effective_ctx, caller=caller)

    max_tokens = rtk_max_tokens if rtk_max_tokens is not None else _RTK_MAX_TOKENS
    before_tokens = _estimate_tokens(code_context)

    # Pipeline duplo: caveman (semântico) → RTK (token-level)
    caveman_out, caveman_err = await caveman_compress(code_context, max_tokens=max_tokens)
    compressed_context, rtk_err = _compress_with_rtk(caveman_out, max_tokens=max_tokens)

    engines = []
    if caveman_err is None:
        engines.append("caveman/phi3")
    if rtk_err is None:
        engines.append("rtk")
    used_engine = "+".join(engines) if engines else "fallback"

    after_tokens = _estimate_tokens(compressed_context)
    reduction = max(0, before_tokens - after_tokens)
    reduction_pct = (reduction / before_tokens * 100) if before_tokens else 0.0

    from datetime import UTC, datetime
    _COMPRESSION_TELEMETRY.append(CompressionRecord(
        ts=datetime.now(UTC).isoformat(),
        engine=used_engine,
        caller=caller,
        ctx=effective_ctx,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        reduction_tokens=reduction,
        reduction_pct=round(reduction_pct, 1),
    ))

    planner_prompt, executor_prompt, local_prompt = _build_planner_executor_prompts(
        query=query,
        compressed_context=compressed_context,
        ctx_name=effective_ctx,
    )

    budget = CONTEXT_BUDGETS.get(caller, 4000)
    response = f"Contexto detectado: {result.display}\n\n"
    response += f"## Contexto relevante\n{code_context}\n\n"
    response += f"## compression\nengine: {used_engine}\n"
    if caveman_err:
        response += f"caveman_note: {caveman_err}\n"
    if rtk_err:
        response += f"rtk_note: {rtk_err}\n"
    response += f"tokens aprox: {before_tokens} -> {after_tokens} (-{reduction_pct:.1f}%)\n\n"
    response += f"## Prompt pronto — Claude (Planner)\n{planner_prompt}\n\n"
    response += f"## Prompt pronto — Codex (Executor)\n{executor_prompt}\n\n"
    response += f"## Prompt pronto — Local (Knowledge Draft)\n{local_prompt}"

    return _truncate(response, budget)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
