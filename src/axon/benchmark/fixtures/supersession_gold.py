"""Gold dataset for the supersession-quality benchmark (dec-115).

A *mixed* set: a handful of hand-curated anchor scenarios — inspired by AXON's
own decision history (Neo4j → dec-101, Ollama → dec-106) — plus a generator that
perturbs each anchor into deterministic variations for breadth.

Each scenario is either:

- a *supersession* case: two decisions in the same scope where the newer one
  revises the older (high lexical overlap), or
- a *control* case: two decisions that share a file but address different
  subjects (low overlap), which must NOT be treated as supersession.

These are synthetic engine fixtures, never vault content (respects D1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from axon.core.decision import Decision

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class SupersessionScenario:
    name: str
    decisions: tuple[Decision, ...]
    query_symbols: tuple[str, ...]
    is_control: bool
    # For supersession cases: the decision that should win and the one that
    # should be demoted. Unused for controls.
    current_id: str | None = None
    stale_id: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


def _decision(
    dec_id: str,
    summary: str,
    *,
    day: int,
    files: tuple[str, ...] = (),
    symbols: tuple[str, ...] = (),
    status: str = "draft",
) -> Decision:
    return Decision(
        id=dec_id,
        timestamp=_EPOCH + timedelta(days=day),
        agent="manual",
        repo="axon",
        summary=summary,
        files=[Path(f) for f in files],
        symbols=list(symbols),
        status=status,  # type: ignore[arg-type]
    )


# --- Curated anchors -------------------------------------------------------

_ANCHORS: tuple[SupersessionScenario, ...] = (
    SupersessionScenario(
        name="neo4j-dropped",
        decisions=(
            _decision(
                "dec-300",
                "graph backend uses neo4j for structural subgraphs",
                day=1,
                files=("src/graph/backend.py",),
                symbols=("GraphStore",),
            ),
            _decision(
                "dec-301",
                "graph backend uses neo4j dropped for qdrant subgraphs",
                day=40,
                files=("src/graph/backend.py",),
                symbols=("GraphStore",),
            ),
        ),
        query_symbols=("GraphStore",),
        is_control=False,
        current_id="dec-301",
        stale_id="dec-300",
        tags=("decision-history",),
    ),
    SupersessionScenario(
        name="ollama-opt-in",
        decisions=(
            _decision(
                "dec-310",
                "ollama local models enabled by default for routing",
                day=2,
                files=("src/router/profiles.py",),
                symbols=("ProviderProfile",),
            ),
            _decision(
                "dec-311",
                "ollama local models enabled opt-in only for routing",
                day=30,
                files=("src/router/profiles.py",),
                symbols=("ProviderProfile",),
            ),
        ),
        query_symbols=("ProviderProfile",),
        is_control=False,
        current_id="dec-311",
        stale_id="dec-310",
        tags=("decision-history",),
    ),
    SupersessionScenario(
        name="validation-flag",
        decisions=(
            _decision(
                "dec-320",
                "decision scored flag uses validation score zero sentinel",
                day=3,
                files=("src/core/decision.py",),
                symbols=("Decision.judged",),
            ),
            _decision(
                "dec-321",
                "decision scored flag uses judged bool not score sentinel",
                day=22,
                files=("src/core/decision.py",),
                symbols=("Decision.judged",),
            ),
        ),
        query_symbols=("Decision.judged",),
        is_control=False,
        current_id="dec-321",
        stale_id="dec-320",
    ),
    SupersessionScenario(
        name="preexisting-status",
        decisions=(
            _decision(
                "dec-330",
                "compression strategy enabled for all turns",
                day=5,
                files=("src/recall/strategy.py",),
                symbols=("recall_context",),
                status="superseded",
            ),
            _decision(
                "dec-331",
                "compression strategy enabled above token threshold",
                day=18,
                files=("src/recall/strategy.py",),
                symbols=("recall_context",),
            ),
        ),
        query_symbols=("recall_context",),
        is_control=False,
        current_id="dec-331",
        stale_id="dec-330",
        tags=("status-honored",),
    ),
    # --- Controls: shared file, unrelated subjects ---
    SupersessionScenario(
        name="control-logging-vs-rename",
        decisions=(
            _decision(
                "dec-340",
                "add structured logging around the indexer",
                day=4,
                files=("src/code/indexer.py",),
                symbols=("Indexer",),
            ),
            _decision(
                "dec-341",
                "rename a private helper inside the indexer",
                day=20,
                files=("src/code/indexer.py",),
                symbols=("Indexer",),
            ),
        ),
        query_symbols=("Indexer",),
        is_control=True,
        tags=("false-positive-guard",),
    ),
    SupersessionScenario(
        name="control-timeout-vs-retry",
        decisions=(
            _decision(
                "dec-350",
                "raise the network timeout for slow pushes",
                day=6,
                files=("src/hooks/git_event.py",),
                symbols=("GitEvent",),
            ),
            _decision(
                "dec-351",
                "add exponential backoff retry on transient errors",
                day=25,
                files=("src/hooks/git_event.py",),
                symbols=("GitEvent",),
            ),
        ),
        query_symbols=("GitEvent",),
        is_control=True,
        tags=("false-positive-guard",),
    ),
)


# --- Generator: deterministic perturbations of each anchor -----------------


def _perturb(anchor: SupersessionScenario, variant: int) -> SupersessionScenario:
    """Shift timestamps and renumber ids to produce a distinct variation.

    The semantic content and scope relationships are preserved, so the expected
    outcome (supersession vs control) is identical to the anchor.
    """
    offset = 100 * (variant + 1)
    day_shift = 7 * (variant + 1)
    id_map = {
        d.id: f"dec-{int(d.id.split('-')[1]) + offset}" for d in anchor.decisions
    }
    new_decisions = tuple(
        Decision(
            id=id_map[d.id],
            timestamp=d.timestamp + timedelta(days=day_shift),
            agent=d.agent,
            repo=d.repo,
            summary=d.summary,
            files=list(d.files),
            symbols=list(d.symbols),
            status=d.status,
        )
        for d in anchor.decisions
    )
    return SupersessionScenario(
        name=f"{anchor.name}-v{variant}",
        decisions=new_decisions,
        query_symbols=anchor.query_symbols,
        is_control=anchor.is_control,
        current_id=id_map.get(anchor.current_id) if anchor.current_id else None,
        stale_id=id_map.get(anchor.stale_id) if anchor.stale_id else None,
        tags=anchor.tags,
    )


def gold_scenarios(*, variants_per_anchor: int = 3) -> tuple[SupersessionScenario, ...]:
    """The full mixed gold set: curated anchors + generated variations."""
    out: list[SupersessionScenario] = list(_ANCHORS)
    for anchor in _ANCHORS:
        for variant in range(variants_per_anchor):
            out.append(_perturb(anchor, variant))
    return tuple(out)
