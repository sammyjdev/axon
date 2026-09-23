"""Extract unlabeled real-decision corpora for the TypeSafe pilot."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from itertools import combinations
from pathlib import Path
from typing import Protocol

import typer

from axon.benchmark.typesafe import questions
from axon.core.decision import Decision
from axon.embedder.engine import EmbedderEngine
from axon.recall.strategy import _scope
from axon.recall.supersession import PairwiseSimilarity, make_embedding_similarity


@dataclass(frozen=True)
class Stratum:
    """One cosine band, with the population count it was drawn from."""

    name: str
    population: int
    sampled: int


@dataclass(frozen=True)
class SupersessionCase:
    case_id: str
    older_id: str
    newer_id: str
    older_summary: str
    newer_summary: str
    older_ts: str
    newer_ts: str
    #: ``older.status`` frozen at extraction. Production mutates it, so reading
    #: it live at eval time made the ``current`` arm irreproducible.
    older_status: str
    shared_scope: list[str]
    cosine: float
    stratum: str
    split: str


@dataclass(frozen=True)
class JudgeCase:
    case_id: str
    summary: str
    repo: str
    files: list[str]
    symbols: list[str]
    split: str


_STRATA = ("low", "mid", "high")
_ALLOWLIST_ERROR = (
    "Repository allowlist is mandatory because restricted material must never leave "
    "the machine; name every permitted repo explicitly."
)


class _DecisionStore(Protocol):
    async def all_decisions(self) -> list[Decision]: ...


class _DecisionEmbedder:
    """Expose decision ids to the cosine adapter while caching vectors by id."""

    def __init__(self, decisions: list[Decision]) -> None:
        self._decisions = {decision.id: decision for decision in decisions}
        self._engine = EmbedderEngine(model_name=questions.EMBEDDER_MODEL)
        self._vectors: dict[str, list[float]] = {}

    def embed_one(self, decision_id: str) -> list[float]:
        if decision_id not in self._vectors:
            self._vectors[decision_id] = self._engine.embed_one(
                self._decisions[decision_id].summary
            )
        return self._vectors[decision_id]


def _require_allowlist(allowed_repos: frozenset[str]) -> None:
    if not allowed_repos:
        raise ValueError(_ALLOWLIST_ERROR)


def _case_id(left_id: str, right_id: str) -> str:
    value = "\0".join(sorted((left_id, right_id)))
    return hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest()


def _stratum(cosine: float) -> str:
    if cosine < questions.SCOPE_SIM_THRESHOLD:
        return "low"
    if cosine < questions.NEAR_DUP_THRESHOLD:
        return "mid"
    return "high"


def _tuning_ids(case_ids: list[str], seed: int) -> set[str]:
    ordered = sorted(
        case_ids,
        key=lambda case_id: hashlib.sha256(f"{seed}:{case_id}".encode()).digest(),
    )
    return set(ordered[: round(len(ordered) * 0.3)])


def _sample_supersession(
    population: dict[str, list[SupersessionCase]],
    *,
    target: int,
    min_low_stratum: int,
    seed: int,
) -> list[SupersessionCase]:
    available_low = len(population["low"])
    if available_low < min_low_stratum:
        raise ValueError(
            f"low stratum requires {min_low_stratum} cases, but only {available_low} were available"
        )
    if target < min_low_stratum:
        raise ValueError("target cannot be smaller than min_low_stratum")

    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    low = list(population["low"])
    rng.shuffle(low)
    sampled = low[:min_low_stratum]
    remaining = low[min_low_stratum:] + population["mid"] + population["high"]
    rng.shuffle(remaining)
    sampled.extend(remaining[: max(0, target - len(sampled))])
    rng.shuffle(sampled)
    return sampled


async def build_supersession_corpus(
    *,
    store: _DecisionStore,
    allowed_repos: frozenset[str],
    similarity: PairwiseSimilarity | None = None,
    target: int = 150,
    min_low_stratum: int = 40,
    seed: int = 20260922,
) -> tuple[list[SupersessionCase], list[Stratum]]:
    """Extract same-repo, overlapping-scope pairs and sample by cosine band."""
    _require_allowlist(allowed_repos)
    decisions = sorted(
        (decision for decision in await store.all_decisions() if decision.repo in allowed_repos),
        key=lambda decision: (decision.repo, decision.id),
    )
    by_repo: dict[str, list[Decision]] = defaultdict(list)
    for decision in decisions:
        by_repo[decision.repo].append(decision)

    pair_similarity = (
        make_embedding_similarity(_DecisionEmbedder(decisions))
        if similarity is None
        else similarity
    )
    population: dict[str, list[SupersessionCase]] = {name: [] for name in _STRATA}
    for repo in sorted(by_repo):
        for left, right in combinations(by_repo[repo], 2):
            shared_scope = sorted(_scope(left) & _scope(right))
            if not shared_scope:
                continue
            if similarity is None:
                cosine = pair_similarity(left.id, right.id)
            else:
                cosine = pair_similarity(left.summary, right.summary)
            older, newer = sorted((left, right), key=lambda item: (item.timestamp, item.id))
            stratum = _stratum(cosine)
            population[stratum].append(
                SupersessionCase(
                    case_id=_case_id(left.id, right.id),
                    older_id=older.id,
                    newer_id=newer.id,
                    older_summary=older.summary,
                    newer_summary=newer.summary,
                    older_ts=older.timestamp.isoformat(),
                    newer_ts=newer.timestamp.isoformat(),
                    older_status=older.status,
                    shared_scope=shared_scope,
                    cosine=cosine,
                    stratum=stratum,
                    split="",
                )
            )

    sampled = _sample_supersession(
        population,
        target=target,
        min_low_stratum=min_low_stratum,
        seed=seed,
    )
    tuning = _tuning_ids([case.case_id for case in sampled], seed)
    sampled = [
        replace(case, split="tuning" if case.case_id in tuning else "holdout") for case in sampled
    ]
    sample_counts = Counter(case.stratum for case in sampled)
    strata = [
        Stratum(name, population=len(population[name]), sampled=sample_counts[name])
        for name in _STRATA
    ]
    return sampled, strata


async def build_judge_corpus(
    *,
    store: _DecisionStore,
    allowed_repos: frozenset[str],
    target: int = 100,
    seed: int = 20260922,
) -> tuple[list[JudgeCase], list[dict]]:
    """Sample draft decisions without exposing incumbent judge output in cases."""
    _require_allowlist(allowed_repos)
    decisions = sorted(
        (
            decision
            for decision in await store.all_decisions()
            if decision.repo in allowed_repos and decision.status == "draft"
        ),
        key=lambda decision: decision.id,
    )
    random.Random(seed).shuffle(decisions)  # noqa: S311 - reproducible sampling
    selected = decisions[:target]
    tuning = _tuning_ids([decision.id for decision in selected], seed)
    cases = [
        JudgeCase(
            case_id=decision.id,
            summary=decision.summary,
            repo=decision.repo,
            files=[str(path) for path in decision.files],
            symbols=list(decision.symbols),
            split="tuning" if decision.id in tuning else "holdout",
        )
        for decision in selected
    ]
    history = [
        {
            "case_id": decision.id,
            "validation_score": decision.validation_score,
            "judged": decision.judged,
        }
        for decision in selected
    ]
    return cases, history


def _write_jsonl(
    path: Path,
    values: Sequence[SupersessionCase | JudgeCase],
) -> None:
    path.write_text("".join(json.dumps(asdict(value), sort_keys=True) + "\n" for value in values))


def _split_summary(cases: Sequence[SupersessionCase | JudgeCase]) -> str:
    counts = Counter(case.split for case in cases)
    return f"splits: tuning={counts['tuning']} holdout={counts['holdout']}"


async def _extract(surface: str, allowed_repos: frozenset[str], out: Path) -> None:
    from axon.store.session_store import SessionStore

    store = SessionStore()
    await store.init()
    try:
        if surface == "supersession":
            supersession_cases, strata = await build_supersession_corpus(
                store=store,
                allowed_repos=allowed_repos,
            )
            out.mkdir(parents=True, exist_ok=True)
            _write_jsonl(out / "supersession_cases.jsonl", supersession_cases)
            (out / "strata.json").write_text(
                json.dumps([asdict(stratum) for stratum in strata], indent=2) + "\n"
            )
            for stratum in strata:
                typer.echo(
                    f"{stratum.name}: population={stratum.population} sampled={stratum.sampled}"
                )
            typer.echo(_split_summary(supersession_cases))
            typer.echo(f"embedder: {questions.EMBEDDER_MODEL}")
            return

        judge_cases, history = await build_judge_corpus(
            store=store,
            allowed_repos=allowed_repos,
        )
        out.mkdir(parents=True, exist_ok=True)
        _write_jsonl(out / "judge_cases.jsonl", judge_cases)
        (out / "judge_history.jsonl").write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n" for item in history)
        )
        typer.echo(f"sampled: {len(judge_cases)}")
        typer.echo(_split_summary(judge_cases))
        typer.echo("embedder: none")
    finally:
        await store.close()


app = typer.Typer(add_completion=False)


@app.command()
def main(
    surface: str = typer.Option(..., "--surface"),
    repos: str | None = typer.Option(None, "--repos"),
    out: Path = typer.Option(Path("data/typesafe-pilot"), "--out"),
) -> None:
    allowed_repos = frozenset(repo.strip() for repo in (repos or "").split(",") if repo.strip())
    try:
        _require_allowlist(allowed_repos)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if surface not in {"supersession", "judge"}:
        raise typer.BadParameter("surface must be supersession or judge", param_hint="--surface")
    asyncio.run(_extract(surface, allowed_repos, out))


if __name__ == "__main__":
    app()
