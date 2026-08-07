import json
from pathlib import Path

from axon.observability.coverage import aggregate_coverage


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _opportunity(repo: str = "axon", tokens: int = 1000, searches: int = 0) -> dict:
    return {
        "ts": "2026-08-07T12:00:00+00:00",
        "session": "s1",
        "kind": "opportunity",
        "repo": repo,
        "path": f"/Users/samdev/dev/{repo}/x.py",
        "est_tokens_full": tokens,
        "requested_limit": None,
        "searches_in_session": searches,
    }


def test_missing_files_yield_empty_aggregate(tmp_path: Path) -> None:
    aggregate = aggregate_coverage(tmp_path / "nope.jsonl", tmp_path / "also-nope.jsonl")

    assert aggregate.opportunities == 0
    assert aggregate.searches == 0
    assert aggregate.coverage_ratio is None


def test_coverage_is_searches_over_all_retrieval_events(tmp_path: Path) -> None:
    opportunities = tmp_path / "opportunities.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    _write(opportunities, [_opportunity() for _ in range(3)])
    _write(chunks, [{"ts": "2026-08-07T12:00:00+00:00", "chunks": []}])

    aggregate = aggregate_coverage(opportunities, chunks)

    assert aggregate.opportunities == 3
    assert aggregate.searches == 1
    assert aggregate.coverage_ratio == 0.25


def test_searches_before_collection_started_are_excluded(tmp_path: Path) -> None:
    """Opportunity collection starts later than search telemetry. Counting the
    whole search history against a fresh opportunity log reports near-100%
    coverage - the exact false comfort this metric exists to prevent."""
    opportunities = tmp_path / "opportunities.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    _write(opportunities, [_opportunity() for _ in range(3)])  # ts = 2026-08-07T12:00
    _write(
        chunks,
        [{"ts": "2026-07-01T00:00:00+00:00", "chunks": []} for _ in range(500)]
        + [{"ts": "2026-08-07T13:00:00+00:00", "chunks": []}],
    )

    aggregate = aggregate_coverage(opportunities, chunks)

    assert aggregate.searches == 1
    assert aggregate.coverage_ratio == 0.25


def test_reads_are_split_by_whether_a_search_came_first(tmp_path: Path) -> None:
    """A read with no prior search means the tool was never tried; a read after
    a search means the search did not answer the question. They are different
    failures and must not be pooled."""
    opportunities = tmp_path / "opportunities.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    _write(
        opportunities,
        [_opportunity(searches=0), _opportunity(searches=0), _opportunity(searches=2)],
    )
    _write(chunks, [{"ts": "t", "chunks": []}, {"ts": "t", "chunks": []}])

    aggregate = aggregate_coverage(opportunities, chunks)

    assert aggregate.reads_without_prior_search == 2
    assert aggregate.reads_after_search == 1


def test_opportunity_tokens_accumulate_per_repo(tmp_path: Path) -> None:
    opportunities = tmp_path / "opportunities.jsonl"
    _write(
        opportunities,
        [
            _opportunity(repo="axon", tokens=1000),
            _opportunity(repo="axon", tokens=500),
            _opportunity(repo="tools/gnomon-eval", tokens=250),
        ],
    )

    aggregate = aggregate_coverage(opportunities, tmp_path / "missing.jsonl")

    assert aggregate.opportunity_tokens == 1750
    assert aggregate.by_repo["axon"] == 2
    assert aggregate.by_repo["tools/gnomon-eval"] == 1


def test_delivered_savings_scales_the_ratio_by_coverage(tmp_path: Path) -> None:
    """The whole point: a 90% per-call ratio at 25% coverage is not 90% saved."""
    opportunities = tmp_path / "opportunities.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    _write(opportunities, [_opportunity() for _ in range(3)])
    _write(chunks, [{"ts": "t", "chunks": []}])

    aggregate = aggregate_coverage(opportunities, chunks)

    assert aggregate.delivered_savings(0.9) == 0.225


def test_delivered_savings_is_undefined_without_events(tmp_path: Path) -> None:
    aggregate = aggregate_coverage(tmp_path / "a.jsonl", tmp_path / "b.jsonl")

    assert aggregate.delivered_savings(0.9) is None


def test_malformed_lines_are_skipped_not_fatal(tmp_path: Path) -> None:
    opportunities = tmp_path / "opportunities.jsonl"
    opportunities.parent.mkdir(parents=True, exist_ok=True)
    opportunities.write_text(
        json.dumps(_opportunity()) + "\nnot json\n\n" + json.dumps(_opportunity()) + "\n",
        encoding="utf-8",
    )

    aggregate = aggregate_coverage(opportunities, tmp_path / "missing.jsonl")

    assert aggregate.opportunities == 2
