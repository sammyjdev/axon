from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from axon.config.runtime import load_runtime_config
from axon.context.compression_quality import compression_quality_note
from axon.context.contracts import ContextPack, select_default_retrieval_strategy
from axon.context.rtk import (
    RTKError,
    compress_text_with_rtk,
    restore_original_with_rtk,
    store_original_with_rtk,
)
from axon.core.decision import Decision
from axon.embedder.engine import EmbedderEngine
from axon.hooks.file_bridge import update_context_file
from axon.observability.compression_telemetry import (
    CompressionRecord,
    CompressionTelemetryStore,
)
from axon.observability.trace_store import TraceStore
from axon.observability.traced_tool import current_trace_recorder, traced_tool
from axon.obsidian.discovery import discover_vault
from axon.obsidian.exporter import export_adr, export_architecture_doc
from axon.policy.core import PolicyRegistry
from axon.recall import recall_context
from axon.router.compressor import caveman_compress_guarded
from axon.store.collections import get_search_collections
from axon.store.graph_store import GraphStore
from axon.store.session_store import ADR, SessionNote, SessionStore
from axon.store.vector_store_factory import make_vector_store

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
_TRACE_STORE = TraceStore(_RUNTIME)

mcp = FastMCP("axon-context-engine")

# Stores são inicializados lazy no primeiro uso
_vector_store: object | None = None
_graph_store: GraphStore | None = None
_session_store: SessionStore | None = None
_embedder: EmbedderEngine | None = None


def _get_embedder() -> EmbedderEngine:
    global _embedder
    if _embedder is None:
        _embedder = EmbedderEngine()
    return _embedder


def _get_graph_embedder() -> object:
    """Embedder for GLYPH graph retrieval: AXON's engine behind the GLYPH port.

    Imports GLYPH lazily (dec-116 #3) so a missing ``glyph-kg`` install can't
    break module import for every other MCP tool.
    """
    from axon.context.graph_source import GlyphEmbedderAdapter

    return GlyphEmbedderAdapter(_get_embedder())


def _get_vector_store():
    global _vector_store
    if _vector_store is None:
        _vector_store = make_vector_store(_RUNTIME)
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


def _record_mcp_tool_call(
    tool_name: str,
    input_text: str,
    output_text: str,
    ctx: str | None = None,
) -> None:
    from datetime import UTC, datetime

    before_tokens = _estimate_tokens(input_text)
    after_tokens = _estimate_tokens(output_text)
    reduction = max(0, before_tokens - after_tokens)
    reduction_pct = (reduction / before_tokens * 100) if before_tokens else 0.0
    _COMPRESSION_TELEMETRY.append(
        CompressionRecord(
            ts=datetime.now(UTC).isoformat(),
            engine=tool_name,
            caller="mcp",
            ctx=ctx,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            reduction_tokens=reduction,
            reduction_pct=round(reduction_pct, 1),
            kind="tool_io",
        )
    )


def _compress_with_rtk(text: str, max_tokens: int) -> tuple[str, str | None]:
    try:
        return compress_text_with_rtk(text, max_tokens=max_tokens), None
    except RTKError as exc:
        return text, str(exc)


def _reversible_enabled() -> bool:
    return os.environ.get("AXON_RTK_REVERSIBLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _build_planner_executor_prompts(
    query: str, compressed_context: str, ctx_name: str | None
) -> tuple[str, str, str]:
    ctx_label = ctx_name or "auto"

    planner = (
        "Você é o planner. Gere um plano executável para agentes Codex em paralelo.\n"
        "Retorne JSON válido com: goal, assumptions, tasks[].\n"
        "Cada task deve conter: task_id, objective, files, dependencies, "
        "acceptance_criteria, tests, risk, rollback.\n\n"
        f"Contexto AXON (ctx={ctx_label}):\n{compressed_context}\n\n"
        f"Solicitação: {query}"
    )
    executor = (
        "Você é o executor Codex. Execute apenas a task recebida.\n"
        "Saída obrigatória: mudanças, arquivos, comandos, testes, próximos passos.\n\n"
        f"Contexto AXON comprimido:\n{compressed_context}\n\n"
        "Task: <COLE_A_TASK_JSON_AQUI>"
    )
    local_knowledge = (
        "Você é um assistente local para preencher arquivos de knowledge vazios "
        "com rascunho útil.\n"
        "Formato: resumo, passos, exemplos curtos, perguntas para aprofundamento.\n\n"
        f"Contexto AXON (ctx={ctx_label}):\n{compressed_context}\n\n"
        f"Tema: {query}"
    )
    return planner, executor, local_knowledge


def _load_retrieval_profile() -> tuple[str | None, str, tuple[str, ...]]:
    from axon.config.runtime import get_active_profile, get_profile, select_capabilities

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
    from axon.router.classifier import TaskType, classify_task_with_source

    task_type = TaskType.CODE_ANALYSIS
    # When the completion model is pinned local (AXON_COMPLETION_MODEL), keep the
    # whole request offline: skip the task classifier, which otherwise routes to a
    # cloud provider under the FREE profile and would send the query off-box. The
    # default strategy (CODE_ANALYSIS) is the same one the except-branch falls back
    # to, so retrieval behaviour is unchanged — only the cloud call is dropped.
    if not os.environ.get("AXON_COMPLETION_MODEL", "").strip():
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
) -> ContextPack:
    contexts = (effective_ctx,) if effective_ctx else strategy.contexts
    segments: list[str] = []
    total_chars = 0

    for hit in hits[: strategy.max_segments]:
        payload = hit.get("payload") or {}
        file_path = payload.get("file_path", "?")
        symbol = payload.get("symbol", "unknown")
        language = payload.get("language", "?")
        score = float(hit.get("score", 0.0))
        content = str(payload.get("content", "")).strip().replace("\n", " ")
        remaining = strategy.max_chars - total_chars
        if remaining <= 0:
            break
        segment = (
            f"### {symbol} ({language})\n"
            f"Arquivo: {file_path}\n"
            f"Score: {score:.3f}\n"
            f"Trecho: {content[:remaining]}"
        ).strip()
        if not segment:
            continue
        segments.append(segment)
        total_chars += len(segment)

    return ContextPack(
        strategy=strategy,
        task_type=task_type,
        profile=profile,
        mode=mode,
        contexts=contexts,
        segments=tuple(segments),
        metadata=(
            ("ctx", effective_ctx or "auto"),
            ("hits", str(len(segments))),
            ("profile", profile or ""),
            ("mode", mode),
        ),
    )


def _format_context_pack(pack: ContextPack) -> str:
    contexts = ",".join(pack.contexts) if pack.contexts else "auto"
    return (
        "## Context pack\n"
        f"strategy: {pack.strategy.name}\n"
        f"task_type: {pack.task_type}\n"
        f"contexts: {contexts}\n"
        f"segments: {len(pack.segments)}"
    )


def _staleness_notes(hits: list[dict]) -> list[str]:
    notes: list[str] = []
    for hit in hits:
        staleness = hit.get("staleness") or {}
        if not isinstance(staleness, dict) or not staleness.get("is_stale"):
            continue
        payload = hit.get("payload") or {}
        symbol = payload.get("symbol", "unknown")
        replacement_id = staleness.get("replacement_id")
        reasons = staleness.get("reasons") or []
        reason = staleness.get("replacement_reason") or ",".join(reasons)
        note = f"- {symbol} stale"
        if replacement_id:
            note += f" -> replacement={replacement_id}"
        if reason:
            note += f" ({reason})"
        notes.append(note)
    return notes


async def _retrieve_context(
    *,
    query: str,
    ctx: str | None,
    language: str | None,
    max_depth: int,
    max_nodes: int,
    max_tokens: int,
) -> tuple[str, ContextPack, list[dict]]:
    strategy, task_type, profile, mode = _select_retrieval_strategy(query, ctx)
    collections = get_search_collections(ctx) if ctx else list(strategy.contexts)
    store = _get_vector_store()
    query_vector = _get_embedder().embed_one(query)
    results = await store.search(
        query_vector=query_vector,
        collections=collections,
        language=language,
        top_k=strategy.max_segments,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_tokens=min(max_tokens, max(1, strategy.max_chars // 4)),
    )

    pack = _build_context_pack(
        strategy=strategy,
        task_type=task_type,
        profile=profile,
        mode=mode,
        effective_ctx=ctx,
        hits=results,
    )
    if not results:
        return "Nenhum resultado encontrado.", pack, results

    lines: list[str] = list(pack.segments)
    top_symbol = (results[0].get("payload") or {}).get("symbol") if results else None
    if top_symbol:
        # Structural "related deps" enrichment over the SQLite source-of-truth
        # (dec-101), not the Redis traverse cache (dec-116 #4). Redis stays as the
        # structural cache for other paths; this read just no longer depends on it.
        store = _get_session_store()
        await store.init()
        subgraph = await store.query_subgraph(top_symbol, depth=max_depth)
        related = [n for n in subgraph.get("nodes", []) if n != top_symbol]
        if related:
            lines.append(f"## Dependencias relacionadas ({max_depth}-step)")
            lines.append(f"Root: {top_symbol}")
            lines.append(f"Nodes: {', '.join(related[:10])}")

    lines.append(_format_context_pack(pack))
    stale_notes = _staleness_notes(results)
    if stale_notes:
        lines.append("## Staleness")
        lines.extend(stale_notes)
    return "\n\n".join(lines), pack, results


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
@traced_tool(risk="read")
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

    ctx: personal | career | knowledge | saas | work
    Para acessar work, ctx='work' é obrigatório e explícito.
    Sem ctx, busca em personal + career + knowledge + saas.
    caller: claude-code | copilot (afeta budget de tokens retornados)
    """
    trace = current_trace_recorder()
    response, pack, hits = await _retrieve_context(
        query=query,
        ctx=ctx,
        language=language,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_tokens=max_tokens,
    )
    if trace is not None:
        trace.append_stage(
            "retrieval",
            payload={
                "strategy": pack.strategy.name,
                "task_type": pack.task_type,
                "mode": pack.mode or "",
                "hit_count": len(hits),
            },
        )
    budget = CONTEXT_BUDGETS.get(caller, 4000)
    trace_id = trace._trace_id if trace is not None else str(uuid.uuid4())
    return _truncate(f"trace_id: {trace_id}\n{response}", budget)


@mcp.tool()
@traced_tool(risk="read")
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
    notes = await store.get_notes(project, limit=10)
    decisions = await store.find_decisions_by_repo(project, limit=10)

    if not memories and not notes and not decisions:
        return f"Nenhuma memória de sessão para projeto '{project}'."

    lines: list[str] = []

    if memories:
        lines.append(f"## Sessões anteriores — {project}\n")
        for m in memories:
            lines.append(f"**{m.created_at}** ({m.raw_turns} turns)")
            lines.append(m.summary)
            lines.append("")

    if notes:
        lines.append("## Notas de sessão\n")
        for n in notes:
            lines.append(f"- **{n.created_at.isoformat()}** {n.body}")

    if decisions:
        lines.append("## Decisões capturadas\n")
        for d in decisions:
            lines.append(f"- **{d.id}** ({d.status}) {d.summary}")

    budget = CONTEXT_BUDGETS.get(caller, 4000)
    return _truncate("\n".join(lines), budget)


@mcp.tool()
@traced_tool(risk="read")
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
@traced_tool(risk="read")
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
@traced_tool(risk="write")
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
@traced_tool(risk="read")
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
    from axon.context.detector import ContextDetector

    session_store = _get_session_store()
    await session_store.init()

    detector = ContextDetector(session_store)
    result = detector.detect(query, cwd=cwd)
    effective_ctx = ctx or result.context
    trace = current_trace_recorder()
    trace_id = trace._trace_id if trace is not None else str(uuid.uuid4())
    trace_store = _TRACE_STORE

    gateway_decision = _POLICY.decide(
        ctx=effective_ctx,
        model="claude-haiku-4-5-20251001",
        caller=caller,
        force_cloud=(caller == "cloud"),
        trace_store=trace_store,
        trace_id=trace_id,
        trace_payload={"stage": "gateway"},
    )
    if not gateway_decision.allowed and caller == "cloud":
        return (
            "Fallback cloud bloqueado por policy central. "
            f"reason_code={gateway_decision.reason_code.value}; "
            f"policy_version={gateway_decision.policy_version}."
        )

    code_context, pack, hits = await _retrieve_context(
        query=query,
        ctx=effective_ctx,
        language=None,
        max_depth=2,
        max_nodes=25,
        max_tokens=rtk_max_tokens if rtk_max_tokens is not None else _RTK_MAX_TOKENS,
    )
    if trace is not None:
        trace.append_stage(
            "retrieval",
            payload={
                "strategy": pack.strategy.name,
                "task_type": pack.task_type,
                "mode": pack.mode or "",
                "hit_count": len(hits),
            },
        )

    if pack.strategy.enable_compression:
        max_tokens = rtk_max_tokens if rtk_max_tokens is not None else _RTK_MAX_TOKENS
        before_tokens = _estimate_tokens(pack.text)

        caveman_out, caveman_err = await caveman_compress_guarded(
            pack.text, max_tokens=max_tokens, ctx=effective_ctx
        )

        compressed_context, rtk_err = _compress_with_rtk(caveman_out, max_tokens=max_tokens)
        rtk_quality_err = compression_quality_note(pack.text, compressed_context)
        if rtk_quality_err:
            compressed_context = caveman_out
            rtk_err = rtk_quality_err

        engines = []
        if caveman_err is None:
            engines.append("caveman/phi3")
        if rtk_err is None:
            engines.append("rtkx")
        used_engine = "+".join(engines) if engines else "fallback"

        # Reversible compression (opt-in): stash the original so the agent can
        # restore it on demand via the `restore_context` tool. Off by default.
        if _reversible_enabled() and used_engine != "fallback":
            handle = store_original_with_rtk(pack.text)
            if handle:
                compressed_context = f"{compressed_context}\n\n[[ccr:{handle}]]"

        after_tokens = _estimate_tokens(compressed_context)
        reduction = max(0, before_tokens - after_tokens)
        reduction_pct = (reduction / before_tokens * 100) if before_tokens else 0.0
    else:
        compressed_context = pack.text
        caveman_err = f"strategy={pack.strategy.name}"
        rtk_err = None
        used_engine = "disabled"
        before_tokens = _estimate_tokens(pack.text)
        after_tokens = before_tokens
        reduction = 0
        reduction_pct = 0.0
    if trace is not None:
        trace.append_stage(
            "compression",
            model=used_engine,
            payload={
                "strategy": pack.strategy.name,
                "before_tokens": before_tokens,
                "after_tokens": after_tokens,
                "reduction_pct": round(reduction_pct, 1),
                "compression_enabled": pack.strategy.enable_compression,
                "caveman_note": caveman_err or "",
                "rtk_note": rtk_err or "",
            },
        )

    from datetime import UTC, datetime

    _COMPRESSION_TELEMETRY.append(
        CompressionRecord(
            ts=datetime.now(UTC).isoformat(),
            engine=used_engine,
            caller=caller,
            ctx=effective_ctx,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            reduction_tokens=reduction,
            reduction_pct=round(reduction_pct, 1),
            kind="compression",
        )
    )

    planner_prompt, executor_prompt, local_prompt = _build_planner_executor_prompts(
        query=query,
        compressed_context=compressed_context,
        ctx_name=effective_ctx,
    )

    budget = CONTEXT_BUDGETS.get(caller, 4000)
    response = f"trace_id: {trace_id}\n"
    response += f"Contexto detectado: {result.display}\n\n"
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


@mcp.tool()
@traced_tool(risk="read")
async def get_graph_neighbors(node: str, depth: int = 1) -> str:
    """Retorna vizinhos de um nó no grafo estrutural de código (SQLite)."""
    store = _get_session_store()
    await store.init()
    subgraph = await store.query_subgraph(node, depth=depth)
    edges = subgraph["edges"]
    if not edges:
        response = "Nenhum vizinho encontrado."
    else:
        response = "\n".join(f"{e['source']} -> {e['target']}" for e in edges)
    _record_mcp_tool_call("get_graph_neighbors", node, response)
    return response


@mcp.tool()
@traced_tool(risk="read")
async def get_graph_path(from_node: str, to_node: str) -> str:
    """Retorna o caminho mais curto entre dois nós no grafo de código (SQLite)."""
    store = _get_session_store()
    await store.init()
    path = await store.shortest_path(from_node, to_node)
    response = " -> ".join(path) if path else "Nenhum caminho encontrado."
    _record_mcp_tool_call("get_graph_path", f"{from_node}\n{to_node}", response)
    return response


@mcp.tool()
@traced_tool(risk="read")
async def get_graph_context(query: str, token_budget: int = 1000) -> str:
    """Contexto graph-aware do código, servido pela lib GLYPH (dec-116).

    Ancora a query no grafo de código consolidado (SQLite) e expande a
    vizinhança via GLYPH, retornando segmentos ordenados por relevância.
    """
    # Lazy GLYPH import (dec-116 #3): degrade only this tool when glyph-kg is
    # absent, instead of failing the whole server at module-import time.
    try:
        from axon.context.graph_source import GraphContextSource
    except ModuleNotFoundError:
        return "GLYPH não instalado; rode `pip install glyph-kg[retrieval]`."
    store = _get_session_store()
    await store.init()
    source = GraphContextSource(store, _get_graph_embedder())
    pack = await source.context(query, token_budget=token_budget)
    response = pack.text if pack.segments else "Nenhum contexto de grafo encontrado."
    response = _truncate(response, token_budget)
    _record_mcp_tool_call("get_graph_context", query, response)
    return response


@mcp.tool()
@traced_tool(risk="read")
async def restore_context(handle: str) -> str:
    """Restaura o conteúdo original (pré-compressão) de um handle CCR via rtkx.

    Com a compressão reversível ligada, o contexto comprimido carrega um marcador
    ``[[ccr:<handle>]]``; passe o ``<handle>`` aqui para recuperar o original
    completo sob demanda, sem reexecutar a recuperação.
    """
    try:
        original = restore_original_with_rtk(handle)
    except RTKError as exc:
        return f"rtkx restore falhou: {exc}"
    _record_mcp_tool_call("restore_context", handle, original)
    return original


# ---------------------------------------------------------------------------
# Session lifecycle (cross-agent)
# ---------------------------------------------------------------------------


def _detect_repo_root() -> Path | None:
    """Best-effort git repo root from the working directory."""
    import subprocess

    try:
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return Path(root)
    except Exception:
        return None


def _detect_repo() -> str:
    """Best-effort repo name from the working directory."""
    root = _detect_repo_root()
    return root.name if root is not None else Path.cwd().name


_DECISION_AGENTS = {"claude-code", "codex", "cursor", "manual"}


def _detect_agent(explicit: str | None = None) -> str:
    """Resolve the calling agent: explicit value, AXON_AGENT env, else 'unknown'."""
    return explicit or os.environ.get("AXON_AGENT") or "unknown"


@mcp.tool()
@traced_tool(risk="write")
async def axon_session_start(agent: str | None = None, repo: str | None = None) -> str:
    """Start an AXON session: recall context for the repo and return it.

    The agent is detected from AXON_AGENT when not given; the repo is detected
    from the working directory. Returns the session id followed by a compact
    recalled-context summary.
    """
    store = _get_session_store()
    await store.init()
    agent = _detect_agent(agent)
    repo = repo or _detect_repo()
    session_id = uuid.uuid4().hex[:12]
    context = await recall_context(repo, store=store)
    await store.save_session(session_id, agent, repo, context_payload=context)
    return f"session: {session_id}\nrepo: {repo}\n\n{context}"


@mcp.tool()
@traced_tool(risk="write")
async def axon_session_end(session_id: str, summary: str | None = None) -> str:
    """End an AXON session. An optional summary is saved as a session note."""
    store = _get_session_store()
    await store.init()
    repo = await store.end_session(session_id)
    if repo is None:
        return f"session {session_id} not found."
    if summary:
        await store.save_note(SessionNote(project=repo, body=summary))
    root = _detect_repo_root()
    if root is not None and root.name == repo:
        await update_context_file(root, store=store)
    return f"session {session_id} ended ({repo})."


@mcp.tool()
@traced_tool(risk="write")
async def axon_capture_event(event_type: str, payload: dict) -> str:
    """Universal event capture (file_edit, plan_end, test_pass, manual_note...).

    The event is persisted as a session note for the payload's repo.
    """
    import json as _json

    store = _get_session_store()
    await store.init()
    repo = str(payload.get("repo") or _detect_repo())
    body = f"[{event_type}] {_json.dumps(payload, sort_keys=True, ensure_ascii=False)}"
    await store.save_note(SessionNote(project=repo, body=body))
    return f"captured {event_type} for {repo}."


# ---------------------------------------------------------------------------
# Cross-agent tools (T6.1)
# ---------------------------------------------------------------------------


@mcp.tool()
@traced_tool(risk="read")
async def axon_get_context(repo: str | None = None, token_budget: int = 2000) -> str:
    """Recall compact, ranked project context (recent decisions) for a repo."""
    store = _get_session_store()
    await store.init()
    repo = repo or _detect_repo()
    return await recall_context(repo, store=store, token_budget=token_budget)


@mcp.tool()
@traced_tool(risk="write")
async def axon_capture(
    summary: str,
    repo: str | None = None,
    files: list[str] | None = None,
    symbols: list[str] | None = None,
    agent: str | None = None,
) -> str:
    """Capture a draft decision into AXON's store. Returns the new decision id."""
    store = _get_session_store()
    await store.init()
    repo = repo or _detect_repo()
    detected = _detect_agent(agent)
    decision = Decision(
        id=await store.next_decision_id(),
        timestamp=datetime.now(UTC),
        agent=detected if detected in _DECISION_AGENTS else "manual",
        repo=repo,
        files=[Path(f) for f in (files or [])],
        symbols=symbols or [],
        summary=summary[:80],
        status="draft",
    )
    await store.save_decision(decision)
    return f"captured {decision.id} for {repo}."


@mcp.tool()
@traced_tool(risk="read")
async def axon_search(query: str, repo: str | None = None) -> str:
    """Search captured decisions by summary text for a repo."""
    store = _get_session_store()
    await store.init()
    repo = repo or _detect_repo()
    decisions = await store.find_decisions_by_repo(repo, limit=200)
    needle = query.lower()
    hits = [d for d in decisions if needle in d.summary.lower()]
    if not hits:
        return f"no decisions matching {query!r} in {repo}."
    return "\n".join(f"- {d.id} ({d.status}): {d.summary}" for d in hits)


@mcp.tool()
@traced_tool(risk="read")
async def axon_handoff(to_agent: str, repo: str | None = None) -> str:
    """Produce a handoff brief for another agent: recalled context + pointer."""
    store = _get_session_store()
    await store.init()
    repo = repo or _detect_repo()
    context = await recall_context(repo, store=store)
    return (
        f"# AXON handoff -> {to_agent}\n"
        f"repo: {repo}\n\n{context}\n\n"
        "Continue via the AXON MCP server or by reading .axon/context.md."
    )


async def _export_repo_docs(store: SessionStore, repo: str) -> str:
    """Export a repo's decisions as ADR + architecture docs to the vault."""
    vault = discover_vault()
    if vault is None:
        return "no Obsidian vault discovered — export skipped."
    decisions = await store.find_decisions_by_repo(repo, limit=200)
    for decision in decisions:
        export_adr(decision, vault=vault)
    arch = export_architecture_doc(decisions, vault=vault, name=repo)
    return f"exported {len(decisions)} decision(s) for {repo} to {arch}."


@mcp.tool()
@traced_tool(risk="destructive")
async def axon_export_now(repo: str | None = None) -> str:
    """Export architecture + ADR docs for a repo's decisions to the vault."""
    store = _get_session_store()
    await store.init()
    return await _export_repo_docs(store, repo or _detect_repo())


@mcp.tool()
@traced_tool(risk="destructive")
async def axon_mark_done(repo: str | None = None) -> str:
    """Mark the current work scope done, then export the repo's docs."""
    store = _get_session_store()
    await store.init()
    repo = repo or _detect_repo()
    await store.save_note(SessionNote(project=repo, body="[scope] marked done"))
    return await _export_repo_docs(store, repo)


@mcp.tool()
@traced_tool(risk="read")
async def axon_validation_stats(
    repo: str | None = None, threshold: float = 3.5
) -> str:
    """Aggregate verification pass rate over judged Decisions."""
    import json as _json

    from axon.validation.aggregate import pass_rate

    store = _get_session_store()
    await store.init()
    stats = await pass_rate(store=store, repo=repo, threshold=threshold)
    if stats is None:
        scope = repo if repo is not None else "workspace"
        return f"no decisions for {scope}."

    trace = current_trace_recorder()
    if trace is not None:
        trace.append_stage(
            "validation_result",
            payload={
                "repo": repo if repo is not None else "",
                "threshold": threshold,
                "n_total": stats.n_total,
                "n_scored": stats.n_scored,
                "n_passed": stats.n_passed,
                "pass_rate": round(stats.pass_rate, 4),
            },
        )
    return _json.dumps(stats.model_dump(), sort_keys=True)


@mcp.tool()
@traced_tool(risk="read")
async def axon_health() -> str:
    """Report the health of each AXON subsystem.

    Covers SQLite, Redis, Qdrant, mem0, the Obsidian vault and git.

    Each external probe is time-bounded so an unreachable backend cannot hang
    the whole report — important when AXON is configured against a host that
    is offline (e.g. an unreachable LAN/Tailscale IP).
    """
    import subprocess

    # Each external probe is wrapped in this budget so the command can never
    # block on a dead socket / unrouteable host.
    _PROBE_TIMEOUT = 2.0

    lines = ["# AXON health"]

    try:
        await asyncio.wait_for(_get_session_store().init(), timeout=_PROBE_TIMEOUT)
        lines.append("- sqlite: ok")
    except TimeoutError:
        lines.append("- sqlite: down (timeout)")
    except Exception as exc:
        lines.append(f"- sqlite: down ({exc})")

    try:
        await asyncio.wait_for(_get_graph_store().connect(), timeout=_PROBE_TIMEOUT)
        lines.append("- redis: ok")
    except TimeoutError:
        lines.append("- redis: down (timeout)")
    except Exception as exc:
        lines.append(f"- redis: down ({exc})")

    try:
        await asyncio.wait_for(
            _get_vector_store().ensure_collections(), timeout=_PROBE_TIMEOUT
        )
        lines.append("- qdrant: ok")
    except TimeoutError:
        lines.append("- qdrant: down (timeout)")
    except Exception as exc:
        lines.append(f"- qdrant: down ({exc})")

    try:
        import mem0  # noqa: F401

        lines.append("- mem0: installed")
    except Exception:
        lines.append("- mem0: not installed")

    vault = discover_vault()
    lines.append(f"- vault: {vault}" if vault else "- vault: not found")

    # Run git in a worker thread: a blocking subprocess.check_output on the
    # asyncio/Proactor event-loop thread can stall for ~30s on Windows (handle
    # inheritance of the stdio pipes), which hung this whole tool. to_thread
    # keeps the loop free so the probe timeout is actually honoured.
    # Probe inside the vault, not the server's process cwd — the vault is what
    # we care about versioning, and the server rarely runs from inside it.
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                subprocess.check_output,
                ["git", "rev-parse", "--is-inside-work-tree"],
                text=True,
                stdin=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=vault if vault else None,
            ),
            timeout=_PROBE_TIMEOUT,
        )
        lines.append("- git: ok")
    except Exception:
        lines.append("- git: not a repo")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
