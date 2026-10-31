from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, overload

from mcp.server.mcpserver import MCPServer

from axon.config.runtime import load_runtime_config, work_projects
from axon.core.decision import SUMMARY_MAX_LEN, Decision
from axon.core.repo_identity import repo_identity
from axon.hooks.file_bridge import update_context_file
from axon.models.lesson import LessonCreate, LessonRecord
from axon.observability.friction import aggregate_friction
from axon.observability.trace_store import TraceStore
from axon.observability.traced_tool import current_trace_recorder, traced_tool
from axon.obsidian.discovery import discover_vault
from axon.recall import recall_context
from axon.store.lessons import LessonStore, resolve_lessons_dsn
from axon.store.outcome_store import OutcomeRecord, OutcomeStore
from axon.store.session_store import (
    ADR,
    ADR_INFERRED_NOTICE,
    SessionNote,
    SessionStore,
)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

CONTEXT_BUDGETS: dict[str, int] = {
    "claude-code": 8000,
    "copilot": 2000,
}

_RUNTIME = load_runtime_config()
_DB_PATH = _RUNTIME.db_path
# Read by traced_tool for every call's trace records.
_TRACE_STORE = TraceStore(_RUNTIME)

mcp = MCPServer("axon-context-engine")

# Stores são inicializados lazy no primeiro uso
_outcome_store: OutcomeStore | None = None
_session_store: SessionStore | None = None
_lesson_store: LessonStore | None = None


def _get_outcome_store() -> OutcomeStore:
    global _outcome_store
    if _outcome_store is None:
        _outcome_store = OutcomeStore(dsn=_RUNTIME.pg_url)
    return _outcome_store


def _get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _session_store = SessionStore(_DB_PATH)
    return _session_store


def _get_lesson_store() -> LessonStore:
    """DSN comes from ``resolve_lessons_dsn()`` - AXON_LESSONS_PG_URL, no
    fallback. Reusing ``_RUNTIME.pg_url`` here (like the other stores above)
    would put a client's lessons in the same database as everything else,
    which is exactly what Task 7's isolation exists to prevent.
    """
    global _lesson_store
    if _lesson_store is None:
        _lesson_store = LessonStore(dsn=resolve_lessons_dsn())
    return _lesson_store


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
async def get_adrs(
    project: str,
    ctx: str | None = None,
    caller: str = "claude-code",
) -> str:
    """
    Retorna ADRs de um projeto.
    Projetos de work só acessíveis com ctx='work'.
    """
    if project.lower() in work_projects() and ctx != "work":
        return "Contexto de trabalho requer ctx='work' explícito."

    store = _get_session_store()
    await store.init()
    adrs = await store.get_adrs(project)
    if not adrs:
        return f"Nenhum ADR para projeto '{project}'."

    lines = [f"## ADRs — {project}\n"]
    for adr in adrs:
        lines.append(f"### {adr.title}")
        if getattr(adr, "provenance", "llm-inferred") != "human":
            lines.append(ADR_INFERRED_NOTICE)
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

    O ADR fica marcado como ``llm-inferred``: o chamador desta tool é um
    agente, não uma pessoa, e nada nesta cadeia verifica autoria humana.
    Somente ``axon adr add``, que passa por um prompt interativo, pode
    registrar autoria humana.
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


# ---------------------------------------------------------------------------
# Session lifecycle (cross-agent)
# ---------------------------------------------------------------------------


def _detect_repo_root() -> Path | None:
    """Best-effort git repo root from the working directory."""
    import subprocess

    try:
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return Path(root)
    except Exception:
        return None


def _detect_repo() -> str:
    """Best-effort repo name from the working directory (dec-129)."""
    return repo_identity()


def _is_path_like(val: str) -> bool:
    """Return True if val looks like a filesystem path."""
    return (
        val in (".", "..")
        or "/" in val
        or (os.sep != "/" and os.sep in val)
        or os.path.isabs(val)
    )


@overload
def _resolve_repo(repo: str | Path | None = None, *, allow_none: Literal[False] = False) -> str: ...
@overload
def _resolve_repo(repo: str | Path | None = None, *, allow_none: Literal[True]) -> str | None: ...
def _resolve_repo(
    repo: str | Path | None = None, *, allow_none: bool = False
) -> str | None:
    """Resolve repo parameter: normalise path-like inputs to repo identity.

    Bare names pass through untouched without invoking git.
    Path-like strings (absolute, containing path separators, or '.'/'..')
    are resolved via repo_identity.
    None falls back to _detect_repo() unless allow_none=True.
    """
    if repo is None:
        return None if allow_none else _detect_repo()
    if isinstance(repo, Path):
        return repo_identity(repo.resolve())
    val = str(repo).strip()
    if not val:
        return None if allow_none else _detect_repo()
    if _is_path_like(val):
        return repo_identity(Path(val).resolve())
    return val


_DECISION_AGENTS = {"claude-code", "codex", "cursor", "manual"}


def _detect_agent(explicit: str | None = None) -> str:
    """Resolve the calling agent: explicit value, AXON_AGENT env, else 'unknown'."""
    return explicit or os.environ.get("AXON_AGENT") or "unknown"


@mcp.tool()
@traced_tool(risk="write")
async def axon_session_start(
    agent: str | None = None,
    repo: str | None = None,
    *,
    activity_session_id: str | None = None,
    activity_turn_id: str | None = None,
    activity_event_id: str | None = None,
) -> str:
    """Start an AXON session: recall context for the repo and return it.

    The agent is detected from AXON_AGENT when not given; the repo is detected
    from the working directory. Returns the session id followed by a compact
    recalled-context summary.
    """
    store = _get_session_store()
    await store.init()
    agent = _detect_agent(agent)
    repo = _resolve_repo(repo)
    session_id = uuid.uuid4().hex[:12]
    context = await recall_context(repo, store=store)
    await store.save_session(session_id, agent, repo, context_payload=context)

    if activity_session_id is not None:
        from axon.activity.repository import PostgresActivityRepository

        activity_repo = PostgresActivityRepository(_RUNTIME.pg_url)
        try:
            await activity_repo.record_evidence_link(
                target_type="context_delivery",
                target_id=session_id,
                session_id=activity_session_id,
                turn_id=activity_turn_id,
                event_id=activity_event_id,
                relation="context-delivered",
            )
        finally:
            await activity_repo.close()

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
    if root is not None and repo_identity(root) == repo:
        try:
            # ponytail: full-file JSONL scan (~5k lines today), partition by month if it grows.
            patterns = aggregate_friction(TraceStore().query(stage="policy"))
        except Exception:
            patterns = ()
        await update_context_file(root, store=store, friction=patterns)
    return f"session {session_id} ended ({repo})."


@mcp.tool()
@traced_tool(risk="write")
async def axon_capture_event(
    event_type: str,
    payload: dict,
    *,
    activity_session_id: str | None = None,
    activity_turn_id: str | None = None,
    activity_event_id: str | None = None,
) -> str:
    """Universal event capture (file_edit, plan_end, test_pass, manual_note...).

    The event is persisted as a session note for the payload's repo.
    """
    import json as _json

    store = _get_session_store()
    await store.init()
    repo = _resolve_repo(payload.get("repo"))
    body = f"[{event_type}] {_json.dumps(payload, sort_keys=True, ensure_ascii=False)}"
    note_id = await store.save_note(SessionNote(project=repo, body=body))

    if activity_session_id is not None:
        from axon.activity.repository import PostgresActivityRepository

        activity_repo = PostgresActivityRepository(_RUNTIME.pg_url)
        try:
            await activity_repo.record_evidence_link(
                target_type="session_note",
                target_id=str(note_id),
                session_id=activity_session_id,
                turn_id=activity_turn_id,
                event_id=activity_event_id,
                relation="record-supported",
            )
        finally:
            await activity_repo.close()

    return f"captured {event_type} for {repo}."


@mcp.tool()
@traced_tool(risk="write")
async def axon_record_outcome(
    summary: str,
    outcome: str,
    repo: str | None = None,
    context: str = "ship",
    tags: list[str] | None = None,
) -> str:
    """Record a shipped-work outcome for outcome-aware recall (FORGE Ship → axon).

    Closes the dev-OS loop: after a PR ships, the orchestrator records what was
    built (``summary``) and how it went (``outcome``) so future recall can learn
    from results, not just intent. ``context`` is a free-text label (default
    ``ship``); it is NOT the restricted axon ctx bucket. Persisted in the
    Postgres OutcomeStore (dec-121). Returns the new outcome id.
    """
    store = _get_outcome_store()
    await store.init()
    repo = _resolve_repo(repo)
    rid = await store.save_outcome(
        OutcomeRecord(
            project=repo,
            context=context,
            summary=summary,
            outcome=outcome,
            tags=tags or [],
        )
    )
    return f"recorded outcome {rid} for {repo} ({context})."


@mcp.tool()
@traced_tool(risk="write")
async def axon_record_lesson(
    kind: str,
    triggers: list[str],
    mistake: str,
    tell: str,
    fix: str,
    source: str,
) -> str:
    """Record a lesson (agent-error or craft-lesson) for later search.

    ``kind`` is either ``agent-error`` or ``craft-lesson``; ``triggers`` are the
    signals that should surface this lesson again and weigh most in search. The
    lesson is stored in the lessons-only database resolved by
    ``resolve_lessons_dsn()`` - never the shared ``AXON_PG_URL`` - so one
    client's lessons cannot land in another's database. Returns the new
    lesson's id.
    """
    create = LessonCreate(
        kind=kind,
        triggers=triggers,
        mistake=mistake,
        tell=tell,
        fix=fix,
        source=source,
    )
    record = LessonRecord(**create.model_dump())

    store = _get_lesson_store()
    await store.init()
    lesson_id = await store.insert(record)
    return f"recorded lesson {lesson_id} ({kind})."


@mcp.tool()
@traced_tool(risk="read")
async def axon_search_lessons(
    query: str,
    kind: str | None = None,
    triggers: list[str] | None = None,
    limit: int = 5,
) -> str:
    """Search recorded lessons by the words of the task at hand.

    Full-text search over each lesson's triggers, mistake, tell and fix, with
    English stemming; any query word can match, and triggers weigh most. Write
    the query in English with the concrete terms of the task (tool, command,
    symptom). Optionally filter by ``kind`` (``agent-error`` / ``craft-lesson``)
    or by ``triggers`` overlap.
    """
    store = _get_lesson_store()
    await store.init()
    results = await store.search(query, kind=kind, triggers=triggers, limit=limit)
    if not results:
        return f"no lessons matching {query!r}."
    return "\n".join(
        f"- {r.id} ({r.kind}, triggers={r.triggers}): {r.mistake} | tell: {r.tell} | fix: {r.fix}"
        for r in results
    )


@mcp.tool()
@traced_tool(risk="read")
async def axon_search_conversations(
    query: str,
    project: str | None = None,
    harness: str | None = None,
    limit: int = 10,
) -> str:
    """Search past agent conversations (Claude Code, Codex) for a phrase.

    Case-insensitive text match over what the person and the agents wrote;
    harness-injected text is left out. Each hit gives the date, session id,
    project/harness, role and a snippet around the match, newest first.
    Filter by ``project`` (repo name) or ``harness`` (``claude-code``/``codex``).
    """
    from axon.activity.conversations import search_conversations
    from axon.activity.repository import PostgresActivityRepository

    repo = PostgresActivityRepository(load_runtime_config().pg_url)
    try:
        await repo.ensure_schema()
        hits = await search_conversations(
            repo, query, project=project, harness=harness, limit=limit
        )
    finally:
        await repo.close()
    return "\n".join(hits) if hits else f"no conversation matching {query!r}."


# ---------------------------------------------------------------------------
# Cross-agent tools (T6.1)
# ---------------------------------------------------------------------------


@mcp.tool()
@traced_tool(risk="read")
async def axon_get_context(repo: str | None = None, token_budget: int = 2000) -> str:
    """Recall compact, ranked project context (recent decisions) for a repo."""
    store = _get_session_store()
    await store.init()
    repo = _resolve_repo(repo)
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
    repo = _resolve_repo(repo)
    detected = _detect_agent(agent)
    decision = Decision(
        id=await store.next_decision_id(),
        timestamp=datetime.now(UTC),
        agent=detected if detected in _DECISION_AGENTS else "manual",
        repo=repo,
        files=[Path(f) for f in (files or [])],
        symbols=symbols or [],
        summary=summary[:SUMMARY_MAX_LEN],
        status="draft",
    )
    decision = await store.add_decision(decision)
    return f"captured {decision.id} for {repo}."


@mcp.tool()
@traced_tool(risk="read")
async def axon_search(query: str, repo: str | None = None) -> str:
    """Search captured decisions by summary text for a repo."""
    store = _get_session_store()
    await store.init()
    repo = _resolve_repo(repo)
    decisions = await store.find_decisions_by_repo(repo, limit=200)
    needle = query.lower()
    hits = [d for d in decisions if needle in d.summary.lower()]
    if not hits:
        return f"no decisions matching {query!r} in {repo}."
    return "\n".join(f"- {d.id} ({d.status}): {d.summary}" for d in hits)


@mcp.tool()
@traced_tool(risk="write")
async def axon_handoff(
    to_agent: str, repo: str | None = None, notes: str | None = None
) -> str:
    """Write a handoff brief for another agent, and return it with its path.

    ``notes`` is what the CALLER knows and the store does not: what this session
    actually did, what is half-finished, what the next agent must not touch.
    Recalled context alone only replays what the store already holds.

    The brief is written to ``<vault>/knowledge/handoffs/`` because a handoff is
    a document, not a decision. A decision is one line - "chose X because Y" -
    and the seventeen handoffs already in the decision store are proof of what
    happens when the two are conflated: summary and body both cap out around 80
    characters, so every one of them is a title cut mid-sentence with the
    content gone. Returning text and hoping someone pastes it somewhere is the
    same failure one step earlier.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    store = _get_session_store()
    await store.init()
    repo = _resolve_repo(repo)
    context = await recall_context(repo, store=store)

    brief = (
        f"# AXON handoff -> {to_agent}\n"
        f"repo: {repo}\n"
        f"date: {datetime.now(UTC).isoformat()}\n\n"
    )
    if notes:
        brief += f"## From this session\n{notes}\n\n"
    brief += (
        f"## Recalled context\n{context}\n\n"
        "Continue via the AXON MCP server or by reading .axon/context.md."
    )

    vault = discover_vault()
    if vault is None:
        # No vault is not a reason to lose the handoff - the caller still gets
        # the text it asked for.
        return brief

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    stamp = datetime.now(UTC).strftime("%H%M%S")
    target = vault / "knowledge" / "handoffs" / f"{day}-{repo}-{to_agent}-{stamp}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(brief, encoding="utf-8")

    return f"{brief}\n\n---\nwritten to: {target}"


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
    repo = _resolve_repo(repo, allow_none=True)
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


async def _probe_postgres() -> None:
    import asyncpg

    con = await asyncpg.connect(_RUNTIME.pg_url)
    try:
        await con.fetchval("SELECT 1")
    finally:
        await con.close()


@mcp.tool()
@traced_tool(risk="read")
async def axon_health() -> str:
    """Report the health of each AXON subsystem.

    Covers Postgres, the Obsidian vault and git.

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
        await asyncio.wait_for(_probe_postgres(), timeout=_PROBE_TIMEOUT)
        lines.append("- postgres: ok")
    except TimeoutError:
        lines.append("- postgres: down (timeout)")
    except Exception as exc:
        lines.append(f"- postgres: down ({exc})")

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
