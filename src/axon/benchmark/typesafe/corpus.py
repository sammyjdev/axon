"""Extract unlabeled real-decision corpora for the TypeSafe pilot."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
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
    """Expose decision ids to the cosine adapter while caching vectors by id.

    Successful vectors are appended to disk so a provider timeout does not
    throw away the run. A failed call is retried three times before raising.
    """

    def __init__(self, decisions: list[Decision], cache_path: Path | None = None) -> None:
        self._decisions = {decision.id: decision for decision in decisions}
        self._engine = EmbedderEngine(model_name=questions.EMBEDDER_MODEL)
        self._cache_path = cache_path
        self._vectors: dict[str, list[float]] = {}
        if cache_path is not None and cache_path.exists():
            for line in cache_path.read_text().splitlines():
                if not line:
                    continue
                item = json.loads(line)
                self._vectors[item["id"]] = item["vector"]

    def embed_one(self, decision_id: str) -> list[float]:
        cached = self._vectors.get(decision_id)
        if cached is not None:
            return cached
        text = self._decisions[decision_id].summary
        error: Exception | None = None
        for attempt in range(3):
            try:
                vector = self._engine.embed_one(text)
            except Exception as exc:
                error = exc
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
                continue
            self._vectors[decision_id] = vector
            if self._cache_path is not None:
                self._cache_path.parent.mkdir(parents=True, exist_ok=True)
                with self._cache_path.open("a") as handle:
                    handle.write(json.dumps({"id": decision_id, "vector": vector}) + "\n")
            return vector
        raise error or RuntimeError("embedding failed")


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


def _summary_key(case: SupersessionCase) -> tuple[str, str]:
    return (case.older_summary.strip(), case.newer_summary.strip())


def _draw(
    items: Sequence[SupersessionCase],
    count: int,
    rng: random.Random,
) -> list[SupersessionCase]:
    pool = list(items)
    rng.shuffle(pool)  # noqa: S311 - reproducible sampling, not security
    return pool[:count]


def _sample_supersession(
    population: dict[str, list[SupersessionCase]],
    *,
    target: int,
    min_low_stratum: int,
    seed: int,
) -> list[SupersessionCase]:
    """Draw a uniform low floor, then a mid census, then high with the rest.

    The draw is ``random.Random(seed)`` shuffled inside each stratum. Spill
    back into unused low only when high cannot fill the target.
    """
    available_low = len(population["low"])
    if available_low < min_low_stratum:
        raise ValueError(
            f"low stratum requires {min_low_stratum} cases, but only {available_low} were available"
        )
    if target < min_low_stratum:
        raise ValueError("target cannot be smaller than min_low_stratum")

    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    low = _draw(population["low"], min_low_stratum, rng)
    budget = target - len(low)
    mid = _draw(population["mid"], min(budget, len(population["mid"])), rng)
    budget -= len(mid)
    high = _draw(population["high"], min(budget, len(population["high"])), rng)
    budget -= len(high)
    if budget > 0:
        used = {case.case_id for case in low}
        low.extend(
            _draw(
                [case for case in population["low"] if case.case_id not in used],
                budget,
                rng,
            )
        )
    sampled = low + mid + high
    rng.shuffle(sampled)
    return sampled


def collapse_duplicate_summaries(
    sampled: list[SupersessionCase],
    population: dict[str, list[SupersessionCase]],
    rng: random.Random,
) -> tuple[list[SupersessionCase], tuple[str, ...]]:
    """Keep one case per identical summary pair and refill from that stratum.

    A repeated commit subject in both splits would let ``current_recalibrated``
    fit its threshold on a copy of a holdout pair. Refill uses the same seed's
    generator, restarted by the caller.
    """
    kept: list[SupersessionCase] = []
    dropped: list[str] = []
    seen: set[tuple[str, str]] = set()
    for case in sorted(sampled, key=lambda item: item.case_id):
        key = _summary_key(case)
        if key in seen:
            dropped.append(case.case_id)
            continue
        seen.add(key)
        kept.append(case)

    kept_ids = {case.case_id for case in kept}
    dropped_ids = set(dropped)
    need = Counter(case.stratum for case in sampled if case.case_id in dropped_ids)
    for stratum in _STRATA:
        if not need[stratum]:
            continue
        pool = [
            case
            for case in population[stratum]
            if case.case_id not in kept_ids
            and case.case_id not in dropped_ids
            and _summary_key(case) not in seen
        ]
        remaining = need[stratum]
        for case in _draw(pool, len(pool), rng):
            key = _summary_key(case)
            if key in seen:
                continue
            seen.add(key)
            kept_ids.add(case.case_id)
            kept.append(case)
            remaining -= 1
            if remaining == 0:
                break
    return kept, tuple(dropped)


def require_pilot_sample(
    cases: Sequence[SupersessionCase],
    *,
    minimum: int = questions.MID_TUNING_MINIMUM,
) -> None:
    """Abort before labeling when the pre-registered sample checks fail.

    The mid floor applies only once that stratum is large enough to be the
    pilot census. Fewer mid cases is a unit-sized corpus, not a failed pilot.
    """
    mid = [case for case in cases if case.stratum == "mid"]
    if len(mid) >= minimum:
        tuning = sum(case.split == "tuning" for case in mid)
        if tuning < minimum:
            raise ValueError(
                f"mid tuning has {tuning} cases, below the pre-registered minimum of {minimum}"
            )
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for case in cases:
        groups[_summary_key(case)].add(case.split)
    crossing = [key for key, splits in groups.items() if len(splits) > 1]
    if crossing:
        older, newer = crossing[0]
        raise ValueError(f"duplicate summaries cross the tuning split: {older!r} / {newer!r}")


async def build_supersession_corpus(
    *,
    store: _DecisionStore,
    allowed_repos: frozenset[str],
    similarity: PairwiseSimilarity | None = None,
    target: int = 150,
    min_low_stratum: int = 40,
    seed: int = questions.SAMPLE_SEED,
    cache_path: Path | None = None,
) -> tuple[list[SupersessionCase], list[Stratum], tuple[str, ...]]:
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
        make_embedding_similarity(_DecisionEmbedder(decisions, cache_path=cache_path))
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
    sampled, dropped = collapse_duplicate_summaries(
        sampled,
        population,
        random.Random(seed),  # noqa: S311 - same pre-registered seed, restarted
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
    return sampled, strata, dropped


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
            out.mkdir(parents=True, exist_ok=True)
            supersession_cases, strata, dropped = await build_supersession_corpus(
                store=store,
                allowed_repos=allowed_repos,
                cache_path=out / "embeddings.jsonl",
            )
            (out / "strata.json").write_text(
                json.dumps([asdict(stratum) for stratum in strata], indent=2) + "\n"
            )
            split_counts = {
                stratum: {
                    "tuning": sum(
                        case.stratum == stratum and case.split == "tuning"
                        for case in supersession_cases
                    ),
                    "holdout": sum(
                        case.stratum == stratum and case.split == "holdout"
                        for case in supersession_cases
                    ),
                }
                for stratum in _STRATA
            }
            try:
                require_pilot_sample(supersession_cases)
                gate_error = None
            except ValueError as exc:
                gate_error = str(exc)
            (out / "sampling.json").write_text(
                json.dumps(
                    {
                        "seed": questions.SAMPLE_SEED,
                        "rule": (
                            "uniform within stratum; low floor, then every mid pair "
                            "that fits, then high"
                        ),
                        "mid_tuning_minimum": questions.MID_TUNING_MINIMUM,
                        "ci": (
                            "Wilson. A zero denominator is undefined, not 0, and the "
                            "holdout comparison on that metric is inconclusive."
                        ),
                        "dropped_duplicate_ids": list(dropped),
                        "split_counts": split_counts,
                        "ok": gate_error is None,
                        "gate_error": gate_error,
                    },
                    indent=2,
                )
                + "\n"
            )
            for stratum, counts in split_counts.items():
                typer.echo(
                    f"{stratum} split: tuning={counts['tuning']} holdout={counts['holdout']}"
                )
            if gate_error is not None:
                (out / "supersession_cases.jsonl").unlink(missing_ok=True)
                typer.echo(gate_error, err=True)
                raise typer.Exit(2)
            _write_jsonl(out / "supersession_cases.jsonl", supersession_cases)
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
