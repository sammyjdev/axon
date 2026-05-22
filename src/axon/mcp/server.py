from __future__ import annotations

import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from axon.config.runtime import load_runtime_config
from axon.context.compression_quality import compression_quality_note
from axon.context.contracts import ContextPack, select_default_retrieval_strategy
from axon.context.rtk import RTKError, compress_text_with_rtk
from axon.embedder.engine import EmbedderEngine
from axon.hooks.file_bridge import update_context_file
from axon.observability.compression_telemetry import (
    CompressionRecord,
    CompressionTelemetryStore,
)
from axon.observability.trace_store import TraceStore
from axon.policy.core import PolicyRegistry
from axon.recall import recall_context
from axon.router.compressor import caveman_compress_guarded
from axon.store.collections import get_search_collections
from axon.store.graph_store import GraphStore
from axon.store.session_store import ADR, SessionNote, SessionStore
from axon.store.vector_store import VectorStore

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
        )
    )


def _compress_with_rtk(text: str, max_tokens: int) -> tuple[str, str | None]:
    try:
        return compress_text_with_rtk(text, max_tokens=max_tokens), None
    except RTKError as exc:
        return text, str(exc)


def _build_planner_executor_prompts(
    query: str, compressed_context: str, ctx_name: str | None
) -> tuple[str, str, str]:
    ctx_label = ctx_name or "auto"

    planner = (
        "Você é o planner. Gere um plano executável para agentes Codex em paralelo.\n"
        "Retorne JSON válido com: goal, assumptions, tasks[].\n"
        "Cada task deve conter: task_id, objective, files, dependencies, "
        "acceptance_criteria, tests, risk, rollback.\n\n"
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
        "Você é um assistente local para preencher arquivos de knowledge vazios "
        "com rascunho útil.\n"
        "Formato: resumo, passos, exemplos curtos, perguntas para aprofundamento.\n\n"
        f"Contexto Prometheus (ctx={ctx_label}):\n{compressed_context}\n\n"
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
        graph = _get_graph_store()
        await graph.connect()
        traversal = await graph.traverse(top_symbol, max_depth=max_depth, max_nodes=max_nodes)
        lines.append("## Dependencias relacionadas (2-step)")
        lines.append(f"Root: {traversal['root']}")
        lines.append(f"Nodes: {', '.join(traversal['nodes'][:10])}")

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
    trace_id = str(uuid.uuid4())
    trace = TraceStore(_RUNTIME).recorder(trace_id=trace_id, caller="mcp", ctx=ctx)
    response, pack, hits = await _retrieve_context(
        query=query,
        ctx=ctx,
        language=language,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_tokens=max_tokens,
    )
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
    return _truncate(f"trace_id: {trace_id}\n{response}", budget)


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
    notes = await store.get_notes(project, limit=10)

    if not memories and not notes:
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
    from axon.context.detector import ContextDetector

    session_store = _get_session_store()
    await session_store.init()

    detector = ContextDetector(session_store)
    result = detector.detect(query, cwd=cwd)
    effective_ctx = ctx or result.context
    trace_id = str(uuid.uuid4())
    trace_store = TraceStore(_RUNTIME)
    trace = trace_store.recorder(trace_id=trace_id, caller=caller, ctx=effective_ctx)

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

        caveman_out, caveman_err = await caveman_compress_guarded(pack.text, max_tokens=max_tokens)

        compressed_context, rtk_err = _compress_with_rtk(caveman_out, max_tokens=max_tokens)
        rtk_quality_err = compression_quality_note(pack.text, compressed_context)
        if rtk_quality_err:
            compressed_context = caveman_out
            rtk_err = rtk_quality_err

        engines = []
        if caveman_err is None:
            engines.append("caveman/phi3")
        if rtk_err is None:
            engines.append("rtk")
        used_engine = "+".join(engines) if engines else "fallback"

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
async def get_graph_path(from_node: str, to_node: str) -> str:
    """Retorna o caminho mais curto entre dois nós no grafo de código (SQLite)."""
    store = _get_session_store()
    await store.init()
    path = await store.shortest_path(from_node, to_node)
    response = " -> ".join(path) if path else "Nenhum caminho encontrado."
    _record_mcp_tool_call("get_graph_path", f"{from_node}\n{to_node}", response)
    return response


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


@mcp.tool()
async def axon_session_start(agent: str, repo: str | None = None) -> str:
    """Start an AXON session: recall context for the repo and return it.

    The repo is detected from the working directory when not given. Returns
    the session id followed by a compact recalled-context summary.
    """
    store = _get_session_store()
    await store.init()
    repo = repo or _detect_repo()
    session_id = uuid.uuid4().hex[:12]
    context = await recall_context(repo, store=store)
    await store.save_session(session_id, agent, repo, context_payload=context)
    return f"session: {session_id}\nrepo: {repo}\n\n{context}"


@mcp.tool()
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
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
