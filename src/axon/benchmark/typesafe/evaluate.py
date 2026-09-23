"""Run the TypeSafe pilot evaluations from corpus artifacts on disk."""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

import typer

from axon.benchmark.typesafe import judge_eval, questions, supersession_eval
from axon.benchmark.typesafe.client import JsonCache, TypeSafeClient
from axon.benchmark.typesafe.corpus import (
    JudgeCase,
    Stratum,
    SupersessionCase,
    _require_allowlist,
)
from axon.core.decision import Decision
from axon.embedder.engine import EmbedderEngine
from axon.recall.supersession import make_embedding_similarity

app = typer.Typer(add_completion=False)


class _DecisionStore(Protocol):
    async def all_decisions(self) -> list[Decision]: ...


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _load_labels(path: Path, *, surface: str) -> dict[str, bool | str]:
    labels: dict[str, bool | str] = {}
    for item in _read_jsonl(path):
        case_id = item.get("case_id")
        label = item.get("label")
        # A tuple, not a set: `in` on a set hashes the left operand first, so a
        # malformed label like ["ok"] would raise TypeError before the ValueError
        # below could name the case id that needs fixing.
        valid = (
            isinstance(label, bool)
            if surface == "supersession"
            else label in ("weak", "ok", "strong")
        )
        if not isinstance(case_id, str) or not valid:
            raise ValueError(f"invalid label for case {case_id!r}: {label!r}")
        labels[case_id] = cast(bool | str, label)
    return labels


def _supersession_case(item: dict) -> SupersessionCase:
    """Build one case, naming the re-extraction a pre-``older_status`` corpus needs.

    ``older_status`` was added on 2026-09-22 so the ``current`` arm stops
    reading a field production mutates. A corpus extracted before that carries
    no status to pin, and guessing one would put the irreproducible number
    back, so this refuses and says how to fix it.
    """
    if "older_status" not in item:
        raise ValueError(
            f"case {item.get('case_id')!r} predates older_status; re-extract the corpus "
            "(python3 -m axon.benchmark.typesafe.corpus ...) - a pinned status cannot be "
            "inferred from an old file"
        )
    return SupersessionCase(**item)


async def load_decisions(
    *,
    store: _DecisionStore,
    allowed_repos: frozenset[str],
    case_ids: set[str],
) -> dict[str, Decision]:
    """Fetch case decisions through the allowlist, failing if any are absent."""
    _require_allowlist(allowed_repos)
    decisions = {
        decision.id: decision
        for decision in await store.all_decisions()
        if decision.repo in allowed_repos and decision.id in case_ids
    }
    missing = sorted(case_ids - decisions.keys())
    if missing:
        raise ValueError(f"decisions missing from store: {', '.join(missing)}")
    return decisions


def _metadata(
    *,
    surface: str,
    samples: int,
    allowed_repos: frozenset[str],
    all_cases: int,
    selected: Sequence[SupersessionCase | JudgeCase],
    skipped: int,
) -> dict:
    splits = Counter(case.split for case in selected)
    return {
        "surface": surface,
        "samples": samples,
        "embedder": questions.EMBEDDER_MODEL,
        "model": questions.MODEL,
        "allowlist": sorted(allowed_repos),
        "counts": {"cases": all_cases, "labelled": len(selected), "skipped": skipped},
        "split_sizes": {"tuning": splits["tuning"], "holdout": splits["holdout"]},
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _print_table(surface: str, report: dict[str, Any]) -> None:
    if surface == "supersession":
        typer.echo("condition | precision | recall | f1")
        for name, value in report["conditions"].items():
            aggregate = value["aggregate"]
            typer.echo(
                f"{name} | {aggregate['precision']:.3f} | {aggregate['recall']:.3f} | "
                f"{aggregate['f1']:.3f}"
            )
        return
    typer.echo("arm | band agreement | parse failures")
    for name, value in report.items():
        if name == "run_metadata":
            continue
        typer.echo(f"{name} | {value['band_agreement']:.3f} | {value['parse_failure_rate']:.3f}")


async def _evaluate(
    *,
    surface: str,
    data: Path,
    labels_path: Path,
    allowed_repos: frozenset[str],
    cache: JsonCache,
    client: TypeSafeClient | None,
    samples: int,
) -> dict:
    raw_labels = _load_labels(labels_path, surface=surface)
    selected_cases: Sequence[SupersessionCase | JudgeCase]
    all_case_count: int
    if surface == "supersession":
        all_supersession_cases = [
            _supersession_case(item)
            for item in _read_jsonl(data / "supersession_cases.jsonl")
        ]
        supersession_cases = [case for case in all_supersession_cases if case.case_id in raw_labels]
        supersession_labels = {
            case.case_id: cast(bool, raw_labels[case.case_id]) for case in supersession_cases
        }
        from axon.store.session_store import SessionStore

        case_ids = {
            decision_id
            for case in supersession_cases
            for decision_id in (case.older_id, case.newer_id)
        }
        store = SessionStore()
        await store.init()
        try:
            decisions = await load_decisions(
                store=store,
                allowed_repos=allowed_repos,
                case_ids=case_ids,
            )
        finally:
            await store.close()
        report = await supersession_eval.run(
            cases=supersession_cases,
            labels=supersession_labels,
            decisions=decisions,
            strata_population={
                item.name: item.population
                for item in (
                    Stratum(**item) for item in json.loads((data / "strata.json").read_text())
                )
            },
            client=client,
            similarity=make_embedding_similarity(
                EmbedderEngine(model_name=questions.EMBEDDER_MODEL)
            ),
            samples=samples,
        )
        selected_cases = supersession_cases
        all_case_count = len(all_supersession_cases)
    else:
        all_judge_cases = [JudgeCase(**item) for item in _read_jsonl(data / "judge_cases.jsonl")]
        judge_cases = [case for case in all_judge_cases if case.case_id in raw_labels]
        judge_labels = {case.case_id: cast(str, raw_labels[case.case_id]) for case in judge_cases}
        report = {
            arm: asdict(value) if is_dataclass(value) else value
            for arm, value in (
                await judge_eval.run(
                    cases=judge_cases,
                    labels=judge_labels,
                    client=client,
                    cache=cache,
                    samples=samples,
                )
            ).items()
        }
        selected_cases = judge_cases
        all_case_count = len(all_judge_cases)
    report["run_metadata"] = _metadata(
        surface=surface,
        samples=samples,
        allowed_repos=allowed_repos,
        all_cases=all_case_count,
        selected=selected_cases,
        skipped=all_case_count - len(selected_cases),
    )
    return report


@app.command()
def main(
    surface: str = typer.Option(..., "--surface"),
    repos: str | None = typer.Option(None, "--repos"),
    data: Path = typer.Option(Path("data/typesafe-pilot"), "--data"),
    labels: Path | None = typer.Option(None, "--labels"),
    samples: int = typer.Option(5, "--samples"),
    no_typesafe: bool = typer.Option(False, "--no-typesafe"),
    out: Path | None = typer.Option(None, "--out"),
) -> None:
    allowed_repos = frozenset(repo.strip() for repo in (repos or "").split(",") if repo.strip())
    try:
        _require_allowlist(allowed_repos)
        if surface not in {"supersession", "judge"}:
            raise ValueError("surface must be supersession or judge")
        if samples < 1:
            raise ValueError("samples must be at least 1")
        if not no_typesafe and not os.environ.get("TYPESAFE_API_KEY"):
            raise ValueError("TYPESAFE_API_KEY is required unless --no-typesafe is set")
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    cache = JsonCache(data / "cache.json")
    client = (
        None if no_typesafe else TypeSafeClient(cache=cache, api_key=os.environ["TYPESAFE_API_KEY"])
    )
    labels_path = labels or data / f"{surface}_labels.jsonl"
    output = out or data / f"report-{surface}.json"
    try:
        report = asyncio.run(
            _evaluate(
                surface=surface,
                data=data,
                labels_path=labels_path,
                allowed_repos=allowed_repos,
                cache=cache,
                client=client,
                samples=samples,
            )
        )
    except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    _print_table(surface, report)
    typer.echo(f"skipped unlabelled cases: {report['run_metadata']['counts']['skipped']}")


if __name__ == "__main__":
    app()
